import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import threading
import time
import webbrowser

import typer
import uvicorn

from autody.chat import (
    ChatPageLoadError,
    DOUYIN_CONFIRMATION_SELECTORS,
    DOUYIN_SELECTORS,
    AuthenticationError,
    DouyinChat,
    FatalChatError,
    login as browser_login,
    open_chat,
)
from autody.account_profile import (
    AccountProfileUnavailable,
    bindings_revalidation_required,
    complete_binding_revalidation,
    mark_bindings_for_revalidation,
    resolve_account_profile,
)
from autody.config import AppConfig, load_config, save_config
from autody.friend_discovery import (
    ScanProgress,
    discover_friends,
    is_discovery_stale,
    load_discovered_friends,
    record_discovery_failure,
)
from autody.locking import SingleInstanceLock, TaskAlreadyRunning
from autody.logging_setup import setup_logging
from autody.messages import read_messages
from autody.preflight import PlaywrightPreflightInspector, PreflightStore, global_failure, run_preflight
from autody.recovery import recovery_due
from autody.retry_state import TaskOutcomeStore
from autody.runner import RunResult, RunStatus, record_safe_pre_send_failure, run_daily
from autody.runtime import configure_runtime, doctor_playwright, repair_playwright
from autody.state import StateStore
from autody.web_api import create_app


app = typer.Typer(no_args_is_help=True, help="抖音每日续火花工具")
BUSY_MESSAGE = "已有 AutoDy 任务正在运行，本次跳过。"
TRIGGER_SOURCES = {"scheduled", "manual", "startup_recovery", "retry"}
SEND_ACTIVITY_MAX_AGE_SECONDS = 6 * 60 * 60


def _project_root(config_path: Path) -> Path:
    return config_path.resolve().parent


def _busy() -> None:
    typer.echo(BUSY_MESSAGE)


def _write_health(config: AppConfig, status: str, detail: str = "") -> None:
    path = config.state_file.parent / "health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"status": status, "detail": detail}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_attention(config: AppConfig, message: str) -> None:
    path = config.state_file.parent / "notifications" / "need-attention.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(message, encoding="utf-8")


def _clear_attention(config: AppConfig) -> None:
    (config.state_file.parent / "notifications" / "need-attention.txt").unlink(missing_ok=True)


def _outcome_store(config: AppConfig) -> TaskOutcomeStore:
    return TaskOutcomeStore(config.state_file.parent / "history" / "task-outcomes.json")


def _final_failure_message(result: RunResult) -> str:
    primary = next(
        (detail for detail in result.target_failures.values() if detail.uncertain_send),
        next(iter(result.target_failures.values()), None),
    )
    if primary is not None:
        prefix = (
            "发送结果不确定，已禁止自动重试"
            if result.status is RunStatus.UNCERTAIN
            else "安全重试已耗尽"
        )
        return (
            f"{prefix}：{primary.user_summary_zh}。"
            f"建议：{primary.suggested_action_zh}。"
        )
    if result.status is RunStatus.UNCERTAIN:
        return "发送结果无法确认，为防止重复发送已停止。建议：查看详情。"
    return "安全重试已耗尽，发送前条件仍不可用。建议：查看详情。"


def _report_daily_result(config: AppConfig, result: RunResult) -> None:
    message = "当天所有目标此前已完成。" if result.status is RunStatus.ALREADY_DONE else f"本次发送完成：成功 {result.sent_count} 个，失败 {result.failed_count} 个。"
    logging.info(message)
    typer.echo(message)
    if result.status is RunStatus.RETRY_PENDING:
        detail = "安全重试已安排；在重试完成前不会显示最终失败通知。"
        logging.warning(detail)
        typer.echo(detail, err=True)
        raise typer.Exit(10)
    if result.status in {RunStatus.FINAL_FAILED, RunStatus.UNCERTAIN}:
        detail = _final_failure_message(result)
        notification_due = bool(result.run_id and _outcome_store(config).notification_due(result.run_id))
        if notification_due:
            _write_attention(config, detail)
            _outcome_store(config).mark_notified(result.run_id)
            typer.echo("AUTODY_FINAL_NOTIFICATION=1")
        logging.error(detail)
        typer.echo(detail, err=True)
        raise typer.Exit(3 if result.status is RunStatus.UNCERTAIN else 2)
    _clear_attention(config)


def _merge_discovered_target_bindings(
    config_path: Path, discovered_config: AppConfig
) -> AppConfig:
    """Merge scan-owned binding fields into the latest user configuration."""
    latest = load_config(config_path)
    for discovered in discovered_config.targets:
        if not discovered.stable_id and not discovered.candidate_id:
            continue
        matches = [
            target
            for target in latest.targets
            if (
                discovered.stable_id
                and target.stable_id == discovered.stable_id
            )
            or (
                discovered.candidate_id
                and target.candidate_id == discovered.candidate_id
            )
        ]
        if not matches:
            matches = [
                target
                for target in latest.targets
                if target.name == discovered.name
                and not target.stable_id
                and not target.candidate_id
            ]
        if len(matches) != 1:
            continue
        target = matches[0]
        if discovered.stable_id:
            target.stable_id = discovered.stable_id
        if discovered.candidate_id:
            target.candidate_id = discovered.candidate_id
    save_config(config_path, latest)
    return latest


def _run_friend_scan(
    loaded: AppConfig,
    config_path: Path,
    discovery_path: Path,
    *,
    force_avatar_refresh: bool,
):
    """Run the read-only browser scan with bounded stages and dashboard progress."""
    progress = ScanProgress(discovery_path)
    overall_deadline = time.monotonic() + loaded.friend_scan_overall_timeout_ms / 1000
    result = None
    try:
        progress.update("waiting_browser")
        with SingleInstanceLock(
            loaded.lock_file,
            timeout_seconds=loaded.friend_scan_lock_timeout_ms / 1000,
        ):
            try:
                remaining_ms = max(1_000, int((overall_deadline - time.monotonic()) * 1000))
                if remaining_ms <= 1_000:
                    raise TimeoutError("friend scan overall deadline expired while waiting for browser")
                progress.update("launching_chromium")
                with open_chat(
                    loaded.profile_dir,
                    min(loaded.page_load_timeout_ms, remaining_ms),
                    True,
                    loaded.artifact_dir,
                    home=_project_root(config_path),
                    on_stage=progress.update,
                ) as page:
                    remaining_ms = max(1, int((overall_deadline - time.monotonic()) * 1000))
                    result = discover_friends(
                        loaded,
                        page,
                        DOUYIN_SELECTORS,
                        discovery_path,
                        force_avatar_refresh=force_avatar_refresh,
                        overall_timeout_ms=remaining_ms,
                        max_scrolls=loaded.friend_scan_max_rounds,
                        avatar_timeout_ms=loaded.avatar_capture_timeout_ms,
                        progress=progress.update,
                    )
                    if result.config_changed:
                        persisted = _merge_discovered_target_bindings(
                            config_path, loaded
                        )
                    else:
                        persisted = loaded
                    complete_binding_revalidation(
                        persisted.state_file.parent,
                        account_scope=result.account_scope,
                        target_candidate_ids=[
                            target.candidate_id for target in persisted.targets
                        ],
                        current_candidate_ids={
                            candidate.candidate_id
                            for candidate in result.candidates
                            if candidate.presence_status == "current"
                        },
                    )
            finally:
                progress.update("releasing_browser_lock")
        progress.finish(
            str(result.last_result.get("status", "completed")),
            rows_found=result.last_result.get("candidates_found", 0),
            avatars_reused=result.last_result.get("avatars_reused", 0),
            avatars_updated=result.last_result.get("avatars_updated", 0),
            avatar_failures=result.last_result.get("avatars_failed", 0),
        )
        return result
    except TaskAlreadyRunning:
        progress.finish("lock_busy")
        raise
    except AuthenticationError:
        progress.finish("login_unavailable")
        raise
    except ChatPageLoadError:
        progress.finish("page_load_failed")
        raise
    except KeyboardInterrupt:
        progress.finish("cancelled")
        raise
    except TimeoutError:
        progress.finish("partial_timeout" if result else "page_load_failed")
        raise
    except Exception:
        progress.finish("page_load_failed")
        raise


def _send_activity_path(config: AppConfig) -> Path:
    return config.lock_file.parent / "daily-send-active.json"


def _sending_active(config: AppConfig) -> bool:
    path = _send_activity_path(config)
    if not path.is_file():
        return False
    try:
        age = datetime.now().timestamp() - path.stat().st_mtime
    except OSError:
        return False
    if age > SEND_ACTIVITY_MAX_AGE_SECONDS:
        path.unlink(missing_ok=True)
        return False
    return True


def _preflight_store(config: AppConfig, data_root: Path | None = None) -> PreflightStore:
    return PreflightStore((data_root or config.state_file.parent) / "preflight")


def _preflight_request_path(config: AppConfig, data_root: Path | None = None) -> Path:
    return (data_root or config.state_file.parent) / "preflight" / "request.json"


def _preflight_cancelled(config: AppConfig, data_root: Path | None = None) -> bool:
    return ((data_root or config.state_file.parent) / "preflight" / "cancel.json").is_file()


def _run_preflight_with_page(
    loaded: AppConfig, page, *, target_ids: list[str] | None, trigger_source: str, data_root: Path | None = None
) -> dict:
    store = _preflight_store(loaded, data_root)
    inspector = PlaywrightPreflightInspector(page, friend_timeout_ms=loaded.friend_search_timeout_ms)
    enabled_count = sum(
        target.enabled and (not target_ids or (target.stable_id or target.candidate_id) in target_ids)
        for target in loaded.targets
    )
    store.save_progress({
        "running": True, "completed_targets": 0, "total_targets": enabled_count,
        "current_status": "checking_chat_page",
    })
    try:
        inspector.chat_ready()
        result = run_preflight(
            loaded, inspector, target_ids=target_ids, trigger_source=trigger_source,
            cancelled=lambda: _preflight_cancelled(loaded, data_root),
            on_progress=store.save_progress,
        )
    except RuntimeError as exc:
        status = "login_required" if str(exc) == "login_required" else "chat_page_unavailable"
        result = global_failure(status, trigger_source=trigger_source, error_summary=str(exc))
    store.save(result)
    return result


def _automatic_preflight_due(loaded: AppConfig) -> bool:
    latest = _preflight_store(loaded).load_latest()
    if not latest or latest.get("trigger_source") != "health_check":
        return True
    if str(latest.get("completed_at", ""))[:10] != datetime.now().date().isoformat():
        return True
    return latest.get("total_targets") == 0 and latest.get("global_status") not in {"ready", "ready_with_warnings"}


@contextmanager
def _sending_activity(config: AppConfig):
    path = _send_activity_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": datetime.now().isoformat(timespec="seconds")}),
        encoding="utf-8",
    )
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@app.command("check-config")
def check_config(config: Path = typer.Option(Path("config.yaml"), "--config")):
    loaded = load_config(config)
    if not loaded.messages_file.exists():
        raise typer.BadParameter("文案库不存在")
    messages = read_messages(loaded.messages_file)
    typer.echo(f"配置有效：{len(loaded.targets)} 个目标，{len(messages)} 条文案")


@app.command()
def login(config: Path = typer.Option(Path("config.yaml"), "--config")):
    loaded = load_config(config)
    try:
        with SingleInstanceLock(loaded.lock_file):
            configure_runtime(_project_root(config))
            setup_logging(loaded)
            typer.echo("浏览器将打开，请扫码登录；检测到聊天列表后会自动保存并关闭。")
            scan_message = "候选好友扫描未启动。"

            def scan_after_login(page) -> None:
                nonlocal scan_message
                try:
                    profile = resolve_account_profile(page, _project_root(config))
                    if profile.switched:
                        mark_bindings_for_revalidation(loaded.state_file.parent)
                    logging.info("当前账号资料已验证并刷新，账号切换=%s", profile.switched)
                except AccountProfileUnavailable as exc:
                    logging.warning("当前账号资料未验证：%s", exc)
                except Exception:
                    logging.exception("当前账号资料刷新失败，但登录仍然有效。")
                try:
                    result = discover_friends(
                        loaded,
                        page,
                        DOUYIN_SELECTORS,
                        loaded.state_file.parent / "discovered_friends.json",
                    )
                    if result.config_changed:
                        persisted = _merge_discovered_target_bindings(config, loaded)
                    else:
                        persisted = loaded
                    complete_binding_revalidation(
                        persisted.state_file.parent,
                        account_scope=result.account_scope,
                        target_candidate_ids=[
                            target.candidate_id for target in persisted.targets
                        ],
                        current_candidate_ids={
                            candidate.candidate_id
                            for candidate in result.candidates
                            if candidate.presence_status == "current"
                        },
                    )
                    scan_message = f"候选好友已刷新：发现 {len(result.candidates)} 个记录。"
                    logging.info(scan_message)
                except Exception as exc:
                    error = str(exc)
                    try:
                        record_discovery_failure(
                            loaded.state_file.parent / "discovered_friends.json",
                            error=error,
                        )
                    except Exception:
                        logging.exception("登录后的候选好友扫描失败，且无法保存失败状态。")
                    scan_message = f"候选好友扫描失败：{error}"
                    logging.warning(scan_message)

            browser_login(
                loaded.profile_dir,
                home=_project_root(config),
                on_ready=scan_after_login,
            )
            _write_health(loaded, "success")
            typer.echo("登录状态已保存。")
            typer.echo(scan_message)
    except TaskAlreadyRunning:
        _busy()


@app.command("refresh-account-profile")
def refresh_account_profile(config: Path = typer.Option(Path("config.yaml"), "--config")):
    """Read and cache the verified current account without opening a profile page."""
    loaded = load_config(config)
    try:
        with SingleInstanceLock(loaded.lock_file):
            configure_runtime(_project_root(config))
            setup_logging(loaded)
            with open_chat(
                loaded.profile_dir,
                timeout_ms=loaded.page_load_timeout_ms,
                headless=loaded.headless,
                home=_project_root(config),
            ) as page:
                profile = resolve_account_profile(page, _project_root(config))
            if profile.switched:
                mark_bindings_for_revalidation(loaded.state_file.parent)
            typer.echo("检测到登录账号已切换，账号资料已更新。" if profile.switched else "当前账号资料已刷新。")
    except AccountProfileUnavailable as exc:
        typer.echo(f"当前账号资料未验证：{exc}")
    except TaskAlreadyRunning:
        _busy()


@app.command("health-check")
def health_check(config: Path = typer.Option(Path("config.yaml"), "--config")):
    loaded = load_config(config)
    try:
        with SingleInstanceLock(loaded.lock_file):
            configure_runtime(_project_root(config))
            setup_logging(loaded)
            with open_chat(
                loaded.profile_dir,
                loaded.page_load_timeout_ms,
                True,
                loaded.artifact_dir,
                home=_project_root(config),
            ) as page:
                logging.info("登录状态和抖音聊天页正常。")
                _write_health(loaded, "success")
                should_recover = loaded.startup_recovery_enabled and recovery_due(
                    loaded, StateStore(loaded.state_file).load(), datetime.now()
                )
                if should_recover and bindings_revalidation_required(
                    loaded.state_file.parent
                ):
                    should_recover = False
                    logging.warning("账号绑定待重新验证，同日恢复发送已暂停。")
                if should_recover:
                    logging.info("检测到今日任务错过，启动同日恢复运行。")
                    chat = DouyinChat(
                        page,
                        DOUYIN_SELECTORS,
                        loaded.artifact_dir,
                        DOUYIN_CONFIRMATION_SELECTORS,
                        confirmation_delay_ms=max(250, loaded.confirmation_timeout_ms // 3),
                        friend_search_timeout_ms=loaded.friend_search_timeout_ms,
                    )
                    with _sending_activity(loaded):
                        run_daily(loaded, chat, trigger_source="startup_recovery")
                else:
                    discovery_path = loaded.state_file.parent / "discovered_friends.json"
                    cached = load_discovered_friends(discovery_path)
                    if is_discovery_stale(cached.scanned_at if cached else None):
                        try:
                            result = discover_friends(
                                loaded, page, DOUYIN_SELECTORS, discovery_path
                            )
                            if result.config_changed:
                                persisted = _merge_discovered_target_bindings(
                                    config, loaded
                                )
                            else:
                                persisted = loaded
                            complete_binding_revalidation(
                                persisted.state_file.parent,
                                account_scope=result.account_scope,
                                target_candidate_ids=[
                                    target.candidate_id
                                    for target in persisted.targets
                                ],
                                current_candidate_ids={
                                    candidate.candidate_id
                                    for candidate in result.candidates
                                    if candidate.presence_status == "current"
                                },
                            )
                            logging.info("登录健康检查已刷新候选好友：发现 %s 个记录。", len(result.candidates))
                        except Exception as exc:
                            record_discovery_failure(discovery_path, str(exc))
                            logging.warning("登录健康检查后的候选好友扫描失败：%s", exc)
                if loaded.preflight_after_health_enabled and _automatic_preflight_due(loaded):
                    try:
                        result = _run_preflight_with_page(
                            loaded, page, target_ids=None, trigger_source="health_check"
                        )
                        logging.info("发送前自检完成：可用 %s，异常 %s", result["ready_count"], result["failed_count"] + result["blocked_count"])
                    except Exception as exc:
                        logging.warning("发送前自检未完成：%s", type(exc).__name__)
    except TaskAlreadyRunning:
        _busy()
        return
    except FatalChatError as exc:
        _write_health(loaded, "failed", str(exc))
        _write_attention(loaded, "抖音登录已失效，请打开 AutoDy 管理台完成扫码登录。")
        logging.error("登录健康检查失败：%s", exc)
        typer.echo(f"登录健康检查失败：{exc}", err=True)
        raise typer.Exit(3) from exc
    except Exception as exc:
        logging.exception("登录健康检查发生未捕获异常。")
        typer.echo("登录健康检查发生未捕获异常，请查看当天日志。", err=True)
        raise typer.Exit(1) from exc
    typer.echo("登录状态正常，聊天页可用。")


@app.command("preflight")
def preflight(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    module_data: Path | None = typer.Option(None, "--module-data"),
):
    """只读检查聊天页面；绝不选择、输入或发送文案。"""
    loaded = load_config(config)
    store = _preflight_store(loaded, module_data)
    request_path = _preflight_request_path(loaded, module_data)
    cancel_path = request_path.parent / "cancel.json"
    try:
        requested = json.loads(request_path.read_text(encoding="utf-8")).get("target_ids") if request_path.is_file() else None
    except (OSError, json.JSONDecodeError):
        requested = None
    cancel_path.unlink(missing_ok=True)
    if _sending_active(loaded):
        store.save(global_failure("browser_busy", trigger_source="manual"))
        typer.echo("AutoDy 正在执行其他浏览器任务，请稍后重试")
        return
    try:
        with SingleInstanceLock(loaded.lock_file):
            configure_runtime(_project_root(config))
            setup_logging(loaded)
            logging.info("发送前自检开始：目标 %s 个", len(requested or [target for target in loaded.targets if target.enabled]))
            with open_chat(
                loaded.profile_dir, loaded.page_load_timeout_ms, loaded.headless,
                home=_project_root(config),
            ) as page:
                result = _run_preflight_with_page(
                    loaded, page, target_ids=requested,
                    trigger_source="test_center" if module_data else "manual", data_root=module_data,
                )
            logging.info("发送前自检完成：可用 %s，异常 %s", result["ready_count"], result["failed_count"] + result["blocked_count"])
            typer.echo(f"发送前自检完成：可用 {result['ready_count']}，异常 {result['failed_count'] + result['blocked_count']}。")
    except TaskAlreadyRunning:
        store.save(global_failure("browser_busy", trigger_source="manual"))
        _busy()
    except AuthenticationError as exc:
        store.save(global_failure("login_required", trigger_source="manual", error_summary=str(exc)))
        typer.echo("抖音登录已失效，请重新登录", err=True)
    except ChatPageLoadError as exc:
        store.save(global_failure("chat_page_unavailable", trigger_source="manual", error_summary=str(exc)))
        typer.echo("抖音聊天页面结构可能已更新", err=True)
    except Exception as exc:
        store.save(global_failure("browser_unavailable", trigger_source="manual", error_summary=type(exc).__name__))
        logging.exception("发送前自检发生未捕获异常。")
        typer.echo("浏览器组件不可用，请运行系统检查", err=True)
        raise typer.Exit(1) from exc


@app.command("scan-friends")
def scan_friends(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    background: bool = typer.Option(False, "--background", help="后台缓存刷新，不重复截取未过期头像"),
):
    loaded = load_config(config)
    discovery_path = loaded.state_file.parent / "discovered_friends.json"
    if _sending_active(loaded):
        typer.echo("每日发送任务正在运行，候选扫描已延后。")
        return
    try:
        configure_runtime(_project_root(config))
        setup_logging(loaded)
        result = _run_friend_scan(
            loaded, config, discovery_path, force_avatar_refresh=not background
        )
        logging.info("好友识别完成：发现 %s 个候选", len(result.candidates))
        typer.echo(f"好友识别完成：发现 {len(result.candidates)} 个候选。")
    except TaskAlreadyRunning:
        _busy()
    except FatalChatError as exc:
        record_discovery_failure(discovery_path, str(exc))
        logging.error("好友识别失败：%s", exc)
        typer.echo(f"好友识别失败：{exc}", err=True)
        raise typer.Exit(3) from exc
    except Exception as exc:
        record_discovery_failure(discovery_path, str(exc))
        logging.exception("好友识别发生未捕获异常。")
        typer.echo("好友识别失败，请查看当天日志。", err=True)
        raise typer.Exit(1) from exc


@app.command("refresh-friend-avatars")
def refresh_friend_avatars(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    """按候选稳定身份重新扫描并校正本地头像关联。"""
    loaded = load_config(config)
    discovery_path = loaded.state_file.parent / "discovered_friends.json"
    try:
        configure_runtime(_project_root(config))
        setup_logging(loaded)
        result = _run_friend_scan(loaded, config, discovery_path, force_avatar_refresh=True)
        message = (
            "扫描超时，已保留上次结果。"
            if result.last_result.get("status") == "partial_timeout"
            else f"头像校正完成：更新 {result.last_result.get('avatars_updated', 0)} 个，失败 {result.last_result.get('avatars_failed', 0)} 个。"
        )
        logging.info(message)
        typer.echo(message)
    except TaskAlreadyRunning:
        _busy()
    except FatalChatError as exc:
        logging.error("头像更新失败：%s", exc)
        typer.echo(f"头像更新失败：{exc}", err=True)
        raise typer.Exit(3) from exc
    except Exception as exc:
        record_discovery_failure(discovery_path, str(exc), status="page_load_failed")
        logging.exception("头像更新发生未捕获异常。")
        typer.echo("头像更新失败，请查看当天日志。", err=True)
        raise typer.Exit(1) from exc


@app.command()
def doctor(config: Path = typer.Option(Path("config.yaml"), "--config")):
    loaded = load_config(config)
    try:
        with SingleInstanceLock(loaded.lock_file):
            result = doctor_playwright(_project_root(config))
    except TaskAlreadyRunning:
        _busy()
        return
    except RuntimeError as exc:
        runtime = configure_runtime(_project_root(config))
        typer.echo(f"Playwright 浏览器目录：{runtime.browsers_path}")
        typer.echo(f"Chromium 启动检查失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"AUTODY_HOME：{result.home}")
    typer.echo(f"Playwright 浏览器目录：{result.browsers_path}")
    typer.echo(f"Chromium 可执行文件：{result.executable_path}")
    typer.echo("Chromium 启动检查：成功")


@app.command("repair-playwright")
def repair_playwright_command(config: Path = typer.Option(Path("config.yaml"), "--config")):
    loaded = load_config(config)
    try:
        with SingleInstanceLock(loaded.lock_file):
            runtime = repair_playwright(_project_root(config))
    except TaskAlreadyRunning:
        _busy()
        return
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Chromium 已重新安装到：{runtime.browsers_path}")


@app.command()
def ui(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    no_open: bool = typer.Option(False, "--no-open"),
):
    config = config.resolve()
    load_config(config)
    configure_runtime(config.parent)
    if host not in {"127.0.0.1", "localhost"}:
        raise typer.BadParameter("管理台只能监听本机地址")
    url = f"http://127.0.0.1:{port}"
    if not no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    typer.echo(f"AutoDy 管理台正在运行：{url}")
    uvicorn.run(create_app(config), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def run(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    source: str = typer.Option("manual", "--source"),
    target_id: str | None = typer.Option(
        None,
        "--target-id",
        help="仅重试具有当前稳定绑定的指定目标",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="只检查任务可启动性，不打开浏览器或发送消息"),
):
    if source not in TRIGGER_SOURCES:
        raise typer.BadParameter(f"未知任务来源：{source}")
    loaded = load_config(config)
    if target_id is not None and not any(
        target.enabled and target.stable_id == target_id
        for target in loaded.targets
    ):
        raise typer.BadParameter("定向重试目标不存在、未启用或缺少稳定标识")
    if dry_run:
        enabled = (
            1
            if target_id is not None
            else sum(1 for target in loaded.targets if target.enabled)
        )
        typer.echo(f"模拟运行已通过：将处理 {enabled} 个启用目标；未打开浏览器，未发送消息。")
        return
    if bindings_revalidation_required(loaded.state_file.parent):
        typer.echo(
            "账号绑定待重新验证，定时发送已暂停。请登录并重新扫描好友。",
            err=True,
        )
        raise typer.Exit(4)
    result: RunResult | None = None
    try:
        with SingleInstanceLock(loaded.lock_file):
            with _sending_activity(loaded):
                configure_runtime(_project_root(config))
                setup_logging(loaded)
                with open_chat(
                    loaded.profile_dir,
                    loaded.page_load_timeout_ms,
                    loaded.headless,
                    loaded.artifact_dir,
                    home=_project_root(config),
                ) as page:
                    chat = DouyinChat(
                        page,
                        DOUYIN_SELECTORS,
                        loaded.artifact_dir,
                        DOUYIN_CONFIRMATION_SELECTORS,
                        confirmation_delay_ms=max(250, loaded.confirmation_timeout_ms // 3),
                        friend_search_timeout_ms=loaded.friend_search_timeout_ms,
                    )
                    result = run_daily(
                        loaded,
                        chat,
                        trigger_source=source,
                        target_ids={target_id} if target_id is not None else None,
                    )
            _write_health(loaded, "success")
            _report_daily_result(loaded, result)
    except TaskAlreadyRunning:
        _report_daily_result(loaded, record_safe_pre_send_failure(loaded, "browser_busy"))
    except AuthenticationError as exc:
        _write_health(loaded, "failed", str(exc))
        _report_daily_result(loaded, record_safe_pre_send_failure(loaded, f"login_unavailable:{exc}"))
    except FatalChatError as exc:
        _report_daily_result(loaded, record_safe_pre_send_failure(loaded, f"browser_unavailable:{exc}"))
    except typer.Exit:
        raise
    except Exception as exc:
        logging.exception("发送任务发生未捕获异常。")
        typer.echo("发送任务发生未捕获异常，请查看当天日志。", err=True)
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
