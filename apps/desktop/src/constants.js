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
/**
 * URL trỏ thẳng tới manifest/segment luồng phát (HLS/DASH).
 * `.m3u8`/`.mpd` là token khó trùng nên chấp nhận ở bất kỳ đâu; `.ts`/`.m4s`
 * quá ngắn nên chỉ tính khi nằm trong PATH — nếu không, một link YouTube dạng
 * `?list=PLx.ts` cũng bị xếp nhầm vào nhóm phim.
 * Giữ đồng bộ với `is_direct_stream_url()` trong core/resolver/url_resolver.py.
 */
export function isDirectStreamUrl(url = '') {
  if (!url || typeof url !== 'string') return false
  if (/\.(?:m3u8|mpd)(?:[?#]|$)/i.test(url)) return true
  const parsed = parseUrlHostname(url)
  const path = parsed?.parsedUrl?.pathname || ''
  return /\.(?:ts|m4s)$/i.test(path) || /\/(?:hls|dash)\//i.test(path)
}

export function detectPlatform(url = '') {
  if (!url || typeof url !== 'string') return null

  // Direct stream URLs -> movie
  if (isDirectStreamUrl(url)) {
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
    const isStream = isDirectStreamUrl(url)
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

/**
 * Định dạng container video — NGUỒN DUY NHẤT cho cả dãy pill nhanh lẫn ô select
 * trong "Tùy chọn tải".
 *
 * Trước đây pill lấy từ `FORMAT_OPTIONS.slice(0, 3)` (mp4/mkv/mov) còn select
 * liệt kê auto/mp4/mkv/webm/gif. Hai danh sách lệch nhau nên: chọn "MOV" bằng
 * pill làm ô select rỗng (không có option khớp), và không có cách nào quay về
 * "Tự động" sau khi đã bấm một pill.
 */
export const VIDEO_CONTAINER_OPTIONS = [
  { id: 'auto', label: 'Tự động', desc: 'Giữ định dạng gốc (khuyên dùng)' },
  { id: 'mp4', label: 'MP4', desc: 'Tương thích cao' },
  { id: 'mkv', label: 'MKV', desc: 'Chất lượng gốc, đa phụ đề' },
  { id: 'mov', label: 'MOV', desc: 'Chuẩn dựng phim Apple' },
  { id: 'webm', label: 'WebM', desc: 'Nhẹ, tối ưu web' },
  { id: 'gif', label: 'GIF', desc: 'Ảnh động' },
]
