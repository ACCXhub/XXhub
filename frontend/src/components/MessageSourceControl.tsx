import type { MessagePack } from "../types";

export function MessageSourceControl({ value, packs, disabled, onChange }: {
  value: string | null;
  packs: Pick<MessagePack, "id" | "name">[];
  disabled: boolean;
  onChange: (id: string | null) => void;
}) {
  return <div className="message-source-control">
    <label>默认发送来源<select aria-label="默认发送来源" value={value ?? ""} disabled={disabled} onChange={(event) => onChange(event.target.value || null)}>
      <option value="">全局文案库</option>
      {value && !packs.some((pack) => pack.id === value) ? <option value={value}>{value}（未加载）</option> : null}
      {packs.map((pack) => <option key={pack.id} value={pack.id}>文案包 · {pack.name}</option>)}
    </select></label>
    <small>好友单独设置优先</small>
  </div>;
}
