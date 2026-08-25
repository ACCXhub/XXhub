export function FriendAvatar({ name, url }: { name: string; url?: string }) {
  const initial = name.trim().slice(0, 1) || "?";
  if (!url) {
    return <span className="friend-avatar avatar-fallback" aria-label={`${name} 的默认头像`}>{initial}</span>;
  }
  return <img key={url} className="friend-avatar" src={url} alt={`${name} 的头像`} loading="lazy" />;
}
