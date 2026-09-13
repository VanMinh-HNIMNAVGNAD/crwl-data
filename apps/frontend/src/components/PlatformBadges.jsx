import { PLATFORMS } from '../constants'

export default function PlatformBadges({
  selectedPlatform,
  activePlatform,
  onSelectPlatform,
  onClearPlatform,
}) {
  const currentActive = selectedPlatform || activePlatform

  return (
    <div className="platform-badges-wrapper">
      <div className="platform-badges-list">
        {selectedPlatform && (
          <button
            type="button"
            className="platform-badge-rect platform-badge-all"
            onClick={() => onClearPlatform && onClearPlatform()}
            title="Hủy chọn nền tảng (Cho phép tải từ mọi nền tảng)"
          >
            <span className="platform-rect-name">✕ Tất cả</span>
          </button>
        )}
        {PLATFORMS.map((p) => {
          const isSelected = selectedPlatform === p.id
          const isActive = currentActive === p.id
          const Icon = p.icon
          return (
            <button
              key={p.id}
              type="button"
              className={`platform-badge-rect platform-badge-${p.id} ${
                isSelected ? 'is-selected is-active' : isActive ? 'is-active' : ''
              }`}
              onClick={() => onSelectPlatform && onSelectPlatform(p.id)}
              title={`${p.name} - ${p.desc}${isSelected ? ' (Đang chọn - Click lại để hủy chọn)' : ''}`}
            >
              <span className="platform-rect-icon">
                <Icon className="w-4 h-4" />
              </span>
              <span className="platform-rect-name">{p.name}</span>
              {isSelected && <span className="platform-rect-dot" />}
            </button>
          )
        })}
      </div>
    </div>
  )
}
