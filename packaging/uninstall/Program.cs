using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Program
{
    private const string ProductCode = "__PRODUCT_CODE__";
    private const string TempFlag = "--from-temp";
    private const string DeleteDataFlag = "--delete-data";

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        if (args.Length > 0 && string.Equals(args[0], TempFlag, StringComparison.OrdinalIgnoreCase))
        {
            var deleteData = args.Length > 1 && string.Equals(args[1], DeleteDataFlag, StringComparison.OrdinalIgnoreCase);
            return RunUninstall(deleteData);
        }

        var confirm = MessageBox.Show(
            "确定要卸载 AutoDy 吗？\n\n程序文件、快捷方式和 AutoDy 计划任务将被移除。",
            "卸载 AutoDy",
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Question,
            MessageBoxDefaultButton.Button2);
        if (confirm != DialogResult.Yes)
        {
            return 0;
        }

        var dataChoice = MessageBox.Show(
            "是否同时删除用户数据？\n\n选择“否”会保留 %LOCALAPPDATA%\\AutoDy 中的配置、消息、日志和本地数据，方便以后重新安装。",
            "卸载 AutoDy",
            MessageBoxButtons.YesNoCancel,
            MessageBoxIcon.Question,
            MessageBoxDefaultButton.Button2);
        if (dataChoice == DialogResult.Cancel)
        {
            return 0;
        }

        try
        {
            var tempDirectory = Path.Combine(Path.GetTempPath(), "AutoDy-Uninstall");
            Directory.CreateDirectory(tempDirectory);
            var tempExe = Path.Combine(tempDirectory, "Uninstall-AutoDy-" + Guid.NewGuid().ToString("N") + ".exe");
            File.Copy(Application.ExecutablePath, tempExe, true);

            Process.Start(new ProcessStartInfo
            {
                FileName = tempExe,
                Arguments = dataChoice == DialogResult.Yes ? TempFlag + " " + DeleteDataFlag : TempFlag,
                UseShellExecute = true
            });
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show("无法启动卸载程序：" + ex.Message, "卸载 AutoDy", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static int RunUninstall(bool deleteData)
    {
        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = "msiexec.exe",
                Arguments = "/x " + ProductCode,
                UseShellExecute = true,
                Verb = "runas"
            };

            using (var process = Process.Start(startInfo))
            {
                if (process == null)
                {
                    MessageBox.Show("无法启动 Windows Installer。", "卸载 AutoDy", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return 1;
                }
                process.WaitForExit();
                if (process.ExitCode != 0 && process.ExitCode != 3010)
                {
                    MessageBox.Show(
                        "Windows Installer 未完成卸载，退出码：" + process.ExitCode,
                        "卸载 AutoDy",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                    return process.ExitCode;
                }
            }
        }
        catch (Win32Exception ex) when (ex.NativeErrorCode == 1223)
        {
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show("卸载失败：" + ex.Message, "卸载 AutoDy", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        if (deleteData)
        {
            var dataRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "AutoDy");
            try
            {
                if (Directory.Exists(dataRoot))
                {
                    Directory.Delete(dataRoot, true);
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "AutoDy 已卸载，但用户数据未能全部删除：\n" + ex.Message,
                    "卸载 AutoDy",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning);
            }
        }

        return 0;
    }
}
