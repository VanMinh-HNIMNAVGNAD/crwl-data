import {
  IconYouTube,
  IconInstagram,
  IconFacebook,
  IconX,
  IconTikTok,
  IconPinterest,
  IconReddit,
  IconSoundcloud,
  IconTwitch,
  IconDailymotion,
  IconBilibili,
  IconMovie
} from './components/Icons.jsx'

export const PLATFORMS = [
  {
    id: 'youtube',
    name: 'YouTube',
    match: ['youtube.com', 'youtu.be'],
    icon: IconYouTube,
    desc: 'Video 4K/2K/1080p, Playlist, Shorts, MP3, Live'
  },
  {
    id: 'instagram',
    name: 'Instagram',
    match: ['instagram.com', 'instagr.am'],
    icon: IconInstagram,
    desc: 'Reels, Stories, Bài viết nhiều ảnh & video'
  },
  {
    id: 'tiktok',
    name: 'TikTok',
    match: ['tiktok.com'],
    icon: IconTikTok,
    desc: 'Video không có logo, Âm thanh gốc, Ảnh Slideshow'
  },
  {
    id: 'facebook',
    name: 'Facebook',
    match: ['facebook.com', 'fb.watch', 'fb.com'],
    icon: IconFacebook,
    desc: 'Watch, Reels, Video HD, Album ảnh'
  },
  {
    id: 'x',
    name: 'X (Twitter)',
    match: ['twitter.com', 'x.com'],
    icon: IconX,
    desc: 'Video bài đăng, Ảnh GIF, Album ảnh HD'
  },
  {
    id: 'pinterest',
    name: 'Pinterest',
    match: ['pinterest.com', 'pin.it'],
    icon: IconPinterest,
    desc: 'Ảnh độ phân giải gốc, Board, Ý tưởng'
  },
  {
    id: 'reddit',
    name: 'Reddit',
    match: ['reddit.com', 'redd.it'],
    icon: IconReddit,
    desc: 'Video Reddit, Thư viện ảnh Subreddit, Gallery'
  },
  {
    id: 'soundcloud',
    name: 'SoundCloud',
    match: ['soundcloud.com'],
    icon: IconSoundcloud,
    desc: 'Track, Playlist, Set âm nhạc MP3/FLAC'
  },
  {
    id: 'twitch',
    name: 'Twitch',
    match: ['twitch.tv', 'clips.twitch.tv'],
    icon: IconTwitch,
    desc: 'VOD, Clips, Highlights từ kênh streamer'
  },
  {
    id: 'dailymotion',
    name: 'Dailymotion',
    match: ['dailymotion.com', 'dai.ly'],
    icon: IconDailymotion,
    desc: 'Video HD từ nền tảng Dailymotion'
  },
  {
    id: 'bilibili',
    name: 'Bilibili',
    match: ['bilibili.com', 'b23.tv'],
    icon: IconBilibili,
    desc: 'Video và anime từ Bilibili (中文)'
  },
  {
    id: 'movie',
    name: 'Phim & HLS',
    match: [
      'motchill', 'phimmoi', 'ophim', 'kkphim', 'subnhanh', 'tvhay', 'bilutv',
      'dongphim', 'xemphim', 'rosetv', 'phim3s', 'hdonline', 'animehay', 'vuighe',
      'fmovies', '123movies', 'soap2day', 'bflix', 'gogoanime', 'aniwatch', 'hianime',
      'lookmovie', 'sflix', 'vidsrc', 'streamtape', 'doodstream',
      'phimhay', 'phimchill', 'phimfast', 'phimhd', 'phimnhanh', 'phimplus',
      'vuphim', 'hdviet', 'kphim', 'phimgio', 'iuphim', 'phimbathu', 'phimmoichill',
      'fullphim', 'phimxuyendem',
    ],
    icon: IconMovie,
    desc: 'Phim trực tuyến, luồng HLS (.m3u8), DASH (.mpd), Video web'
  },
]

export const GENERIC_SHORTENER_DOMAINS = [
  'bit.ly',
  'tinyurl.com',
  't.ly',
  'cutt.ly',
  'is.gd',
  'v.gd',
  'rb.gy',
  'shorturl.at',
  'goo.gl',
  'ow.ly',
  'buff.ly',
  'clck.ru',
  'rebrand.ly',
  'bl.ink',
  'lnkd.in',
  'snip.ly',
  's.id',
  'linktr.ee',
  'shorte.st',
  'adf.ly',
]

/**
 * Phân tích cú pháp URL một cách an toàn bằng đối tượng WHATWG URL
 * Tuyệt đối không dùng so sánh chuỗi regex/substring thông thường
 */
export function parseUrlHostname(rawUrl = '') {
  if (!rawUrl || typeof rawUrl !== 'string') return null
  let trimmed = rawUrl.trim()
  if (!trimmed) return null
  if (!/^https?:\/\//i.test(trimmed)) {
    trimmed = 'https://' + trimmed
  }
  try {
    const parsed = new URL(trimmed)
    const hostname = parsed.hostname.toLowerCase().replace(/\.+$/, '')
    return { hostname, parsedUrl: parsed }
  } catch {
    return null
  }
}

/**
 * Kiểm tra xem hostname có khớp chuẩn xác với targetDomain hay không
 * Chuẩn: hostname === targetDomain HOẶC hostname.endsWith('.' + targetDomain)
 * Tránh hoàn toàn việc bypass chuỗi (ví dụ: attacker.com/?v=youtube.com hoặc fake-youtube.com)
 */
export function isMatchingDomain(hostname, targetDomain) {
  if (!hostname || !targetDomain) return false
  const cleanTarget = targetDomain.toLowerCase().replace(/\.+$/, '')
  return hostname === cleanTarget || hostname.endsWith('.' + cleanTarget)
}

/**
 * Nhận diện nền tảng dựa trên phân tích hostname chuẩn của URL
 */
export function detectPlatform(url = '') {
  if (!url || typeof url !== 'string') return null
  const lower = url.trim().toLowerCase()

  // Direct stream URLs -> movie
  if (lower.includes('.m3u8') || lower.includes('.mpd') || lower.includes('/hls/') ||
      lower.includes('.ts?') || lower.includes('.m4s')) {
    return 'movie'
  }

  const parsed = parseUrlHostname(url)
  if (!parsed) return null
  const { hostname } = parsed

  // Kiểm tra các nền tảng đã biết
  for (const p of PLATFORMS) {
    if (p.id === 'movie') {
      if (p.match.some((m) => hostname.includes(m))) {
        return 'movie'
      }
      continue
    }
    if (p.match.some((m) => isMatchingDomain(hostname, m))) {
      return p.id
    }
  }

  if (isMatchingDomain(hostname, 'threads.net')) return 'threads'
  if (isMatchingDomain(hostname, 'bsky.app')) return 'bluesky'
  if (isMatchingDomain(hostname, 'pixiv.net') || isMatchingDomain(hostname, 'pixiv.me')) return 'pixiv'
  if (isMatchingDomain(hostname, 'deviantart.com')) return 'deviantart'
  if (isMatchingDomain(hostname, 'artstation.com')) return 'artstation'
  if (isMatchingDomain(hostname, 'tumblr.com')) return 'tumblr'
  if (isMatchingDomain(hostname, 'weibo.com')) return 'weibo'
  if (isMatchingDomain(hostname, 'nicovideo.jp') || isMatchingDomain(hostname, 'nico.ms')) return 'niconico'

  // Heuristic: nhận diện trang phim/media chưa biết qua keyword hostname
  const movieHostnameKeywords = [
    'phim', 'movie', 'cinema', 'stream', 'film', 'series',
    'anime', 'xemphim', 'vietsub', 'thuyetminh',
  ]
  if (movieHostnameKeywords.some((k) => hostname.includes(k))) {
    return 'movie'
  }

  // Heuristic: nhận diện trang nhạc chưa biết
  const musicHostnameKeywords = ['nhac', 'music', 'audio', 'nhacviet', 'beatvn', 'soundvn']
  if (musicHostnameKeywords.some((k) => hostname.includes(k))) {
    return 'movie'
  }

  // Heuristic: nhận diện qua path chứa keyword phim
  const { pathname } = parsed.parsedUrl
  const pathLower = (pathname || '').toLowerCase()
  const moviePathKeywords = ['episode', 'tap-', 'phan-', 'season', 'watch', 'play', 'vietsub', 'thuyet-minh']
  if (moviePathKeywords.some((k) => pathLower.includes(k))) {
    return 'movie'
  }

  return null
}

/**
 * Tìm thông tin nền tảng theo ID hoặc alias (hỗ trợ x / twitter)
 */
export function getPlatform(id) {
  if (!id) return null
  const clean = String(id).toLowerCase()
  return PLATFORMS.find((p) => p.id === clean || (p.id === 'x' && clean === 'twitter')) || null
}

/**
 * Kiểm tra xem liên kết có phải từ một dịch vụ rút gọn link chung hay không
 */
export function isGenericShortenerUrl(url = '') {
  const parsed = parseUrlHostname(url)
  if (!parsed) return false
  return GENERIC_SHORTENER_DOMAINS.some((d) => isMatchingDomain(parsed.hostname, d))
}

/**
 * Kiểm tra tính hợp lệ của liên kết so với nền tảng được chọn
 * Trả về chi tiết: matched (khớp), mismatched (sai nền tảng), needs_resolve (cần giải mã link rút gọn), invalid_url
 */
export function validatePlatformUrl(url = '', expectedPlatformId = null) {
  if (!url || !url.trim()) {
    return { valid: true, status: 'empty', message: '' }
  }

  const parsed = parseUrlHostname(url)
  if (!parsed) {
    return {
      valid: false,
      status: 'invalid_url',
      message: 'Định dạng URL không hợp lệ! Vui lòng nhập link đầy đủ (ví dụ: https://...)',
    }
  }

  // 1. Kiểm tra nếu là liên kết rút gọn chung (bit.ly, tinyurl, v.v.)
  const isGeneric = isGenericShortenerUrl(url)
  if (isGeneric) {
    return {
      valid: true,
      status: 'needs_resolve',
      isShortener: true,
      isGeneric: true,
      message: `Phát hiện liên kết rút gọn (${parsed.hostname}). Đang giải mã điểm đến...`,
    }
  }

  // 2. Nếu người dùng không chọn nền tảng cụ thể (chế độ tự do)
  if (!expectedPlatformId) {
    const detected = detectPlatform(url)
    return {
      valid: true,
      status: detected ? 'matched' : 'ok',
      platform: detected,
      message: detected ? `Nhận diện nền tảng: ${detected.toUpperCase()}` : '',
    }
  }

  const expectedPlatform = PLATFORMS.find((p) => p.id === expectedPlatformId)
  const expectedName = expectedPlatform ? expectedPlatform.name : expectedPlatformId.toUpperCase()

  // 1. Kiểm tra riêng cho nền tảng Phim & HLS
  if (expectedPlatformId === 'movie') {
    const isStream = url.toLowerCase().includes('.m3u8') || url.toLowerCase().includes('.mpd') || url.toLowerCase().includes('/hls/')
    const detectedOther = detectPlatform(url)
    if (detectedOther && detectedOther !== 'movie') {
      const otherPlatform = PLATFORMS.find((p) => p.id === detectedOther)
      const otherName = otherPlatform ? otherPlatform.name : detectedOther.toUpperCase()
      return {
        valid: false,
        status: 'mismatched',
        platform: detectedOther,
        expected: expectedPlatformId,
        message: `Bạn đang chọn ${expectedName}, nhưng liên kết này lại thuộc về ${otherName}!`,
      }
    }
    return {
      valid: true,
      status: 'matched',
      platform: 'movie',
      message: isStream ? 'Luồng phát HLS / m3u8 hợp lệ' : 'Liên kết trang phim / video hợp lệ',
    }
  }

  // 2. Kiểm tra trực tiếp xem hostname có khớp đúng nền tảng được chọn không
  const isDirectMatch = expectedPlatform?.match?.some((m) => isMatchingDomain(parsed.hostname, m))
  if (isDirectMatch) {
    return {
      valid: true,
      status: 'matched',
      platform: expectedPlatformId,
      message: `Liên kết chính xác của ${expectedName}`,
    }
  }

  // 2. Kiểm tra xem URL có thuộc về một nền tảng xã hội KHÁC hay không
  const detectedOther = detectPlatform(url)
  if (detectedOther && detectedOther !== expectedPlatformId) {
    const otherPlatform = PLATFORMS.find((p) => p.id === detectedOther)
    const otherName = otherPlatform ? otherPlatform.name : detectedOther.toUpperCase()
    return {
      valid: false,
      status: 'mismatched',
      platform: detectedOther,
      expected: expectedPlatformId,
      message: `Bạn đang chọn ${expectedName}, nhưng liên kết này lại thuộc về ${otherName}!`,
    }
  }

  // 3. Domain ngoài chưa rõ đối với nền tảng được chọn
  // Không được chặn! Cần gửi lên backend để giải mã chuyển hướng HTTP
  return {
    valid: true,
    status: 'needs_resolve',
    isShortener: false,
    isGeneric: false,
    message: 'Đang kiểm tra liên kết chuyển hướng...',
  }
}

export const FORMAT_OPTIONS = [
  { id: 'mp4', label: 'MP4 (Full HD/4K)', desc: 'Video tiêu chuẩn có âm thanh đầy đủ', type: 'video' },
  { id: 'mkv', label: 'MKV (Đa phụ đề/Lossless)', desc: 'Container tối ưu giữ trọn vẹn phụ đề và âm thanh gốc', type: 'video' },
  { id: 'mov', label: 'MOV (Apple QuickTime)', desc: 'Video chuẩn dựng phim Apple / Premiere Pro', type: 'video' },
  { id: 'webm', label: 'WEBM', desc: 'Video nén dung lượng nhẹ, tối ưu web', type: 'video' },
  { id: 'avi', label: 'AVI', desc: 'Định dạng tương thích màn hình ô tô và thiết bị cũ', type: 'video' },
  { id: 'gif', label: 'GIF (Ảnh động)', desc: 'Chuyển đổi video clip thành ảnh động GIF', type: 'video' },
  { id: 'mp3', label: 'MP3 (320kbps)', desc: 'Âm thanh phổ biến chất lượng cao', type: 'audio' },
  { id: 'opus', label: 'OPUS (Chuẩn gốc)', desc: 'Âm thanh chất lượng gốc YouTube không bị nén lại', type: 'audio' },
  { id: 'm4a', label: 'M4A / AAC', desc: 'Âm thanh chuẩn nén cho thiết bị Apple / Mobile', type: 'audio' },
  { id: 'flac', label: 'FLAC (Lossless)', desc: 'Âm thanh phòng thu chất lượng cao nhất', type: 'audio' },
  { id: 'wav', label: 'WAV (Uncompressed)', desc: 'Bản ghi âm thanh không nén chất lượng cao', type: 'audio' },
  { id: 'ogg', label: 'OGG (Vorbis)', desc: 'Âm thanh mã nguồn mở chất lượng cao', type: 'audio' },
  { id: 'aac', label: 'AAC', desc: 'Luồng âm thanh AAC nguyên bản', type: 'audio' },
  { id: 'alac', label: 'ALAC (Apple Lossless)', desc: 'Âm thanh chất lượng cao cho Apple Music', type: 'audio' },
]

export const AUDIO_BITRATES = [
  { id: '320k', label: '320 kbps (Chất lượng cao nhất)' },
  { id: '256k', label: '256 kbps (Chuẩn Studio)' },
  { id: '192k', label: '192 kbps (Chuẩn Phổ biến)' },
  { id: '128k', label: '128 kbps (Tiết kiệm dung lượng)' },
]

/**
 * Danh sách trình duyệt hỗ trợ cookies (phải khớp với backend SupportedBrowser)
 */
export const BROWSER_OPTIONS = [
  { id: 'firefox', label: 'Firefox', desc: 'Mozilla Firefox' },
  { id: 'chrome', label: 'Chrome', desc: 'Google Chrome' },
  { id: 'chromium', label: 'Chromium', desc: 'Chromium (open source)' },
  { id: 'edge', label: 'Edge', desc: 'Microsoft Edge' },
  { id: 'brave', label: 'Brave', desc: 'Brave Browser' },
  { id: 'opera', label: 'Opera', desc: 'Opera Browser' },
  { id: 'vivaldi', label: 'Vivaldi', desc: 'Vivaldi Browser' },
  { id: 'safari', label: 'Safari', desc: 'Apple Safari (macOS)' },
  { id: 'none', label: 'Không dùng', desc: 'Không sử dụng cookies trình duyệt' },
]

/**
 * Nền tảng hỗ trợ live stream
 */
export const LIVE_PLATFORMS = ['youtube', 'twitch', 'facebook', 'instagram', 'tiktok']
