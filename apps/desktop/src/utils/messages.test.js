import test from 'node:test'
import assert from 'node:assert/strict'
import { describeError, fileNameOf, shortenMiddle } from './messages.js'

const looksRaw = (text) => /ERROR:|Traceback|Stderr|--cookies|HTTP Error|\[youtube\]/.test(text)

test('lỗi yt-dlp tiếng Anh không bao giờ hiện nguyên văn ở tiêu đề / gợi ý', () => {
  const raw =
    'yt-dlp extract thất bại: ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you’re not a bot. ' +
    'Use --cookies-from-browser or --cookies for the authentication. See  ' +
    'https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies.'
  const res = describeError(new Error(raw))
  assert.equal(res.title, 'Nội dung YouTube cần đăng nhập')
  assert.match(res.message, /Cookie/)
  assert.ok(!looksRaw(res.title) && !looksRaw(res.message))
  // Chi tiết gốc vẫn được giữ để xem khi cần
  assert.equal(res.detail, raw)
})

test('lỗi đăng nhập của Python giữ tên nền tảng, chi tiết tách riêng', () => {
  const raw =
    'Nội dung Instagram yêu cầu đăng nhập. Vui lòng vào Cookie Manager (🍪), chọn tab Instagram và dán ' +
    'cookie từ trình duyệt. (Chi tiết: Requested content is not available, rate-limit reached or login required)'
  const res = describeError(raw)
  assert.equal(res.title, 'Nội dung Instagram cần đăng nhập')
  assert.match(res.message, /tab Instagram/)
  assert.equal(res.detail, raw)
})

test('câu "cần đăng nhập hoặc bị chặn" của Rust được phân loại theo nguyên nhân thật', () => {
  const raw =
    'Nội dung yêu cầu đăng nhập hoặc bị chặn. Hãy mở Cookie Manager (🍪) để lưu cookie cho nền tảng này ' +
    'rồi thử lại. (Chi tiết: unable to download video data: HTTP Error 403: Forbidden)'
  assert.equal(describeError(raw).title, 'Máy chủ từ chối cho tải')
})

test('các nhóm lỗi thường gặp', () => {
  const cases = [
    ['Timeout: máy chủ bóc tách không phản hồi sau 120 giây. Hãy giảm số lượng cần quét rồi thử lại.', 'Quá thời gian chờ', /giảm số bài/],
    ['Python worker đã dừng đột ngột (exit status: 1). Stderr: Traceback (most recent call last):\n  File "x.py"', 'Bộ phân tích liên kết đang gặp sự cố', /Khởi động lại/],
    ["Không tìm thấy yt-dlp trên máy. Mở 'Công cụ' để kiểm tra, hoặc cài bằng: pipx install yt-dlp", 'Chưa cài yt-dlp', /Công cụ/],
    ['ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path using --ffmpeg-location', 'Chưa cài FFmpeg', /Công cụ/],
    ['Không thể khởi động Python worker #1: No such file or directory (os error 2)', 'Chưa cài Python 3', /Python 3/],
    ['Liên kết này là một danh sách «Mix» gồm 25 mục (playlist, kênh hoặc trang nhiều video), không phải một video đơn. Hãy dán liên kết vào khung ...', 'Liên kết là danh sách 25 video', /Tải theo tài khoản/],
    ['Không đọc được cookie từ trình duyệt đã chọn. Hãy chọn đúng trình duyệt. (Chi tiết: could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271)', 'Không đọc được cookie của trình duyệt', /Tắt Cookies/],
    ['ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader', 'Nội dung không còn tồn tại', /xoá/],
    ['ERROR: [generic] Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name resolution>', 'Lỗi kết nối mạng', /Internet/],
    ['ERROR: [youtube] abc: Requested format is not available. Use --list-formats for a list of available formats', 'Định dạng đã chọn không còn khả dụng', /chất lượng khác/],
    ['Đã tải 3 tệp nhưng KHÔNG nén được ZIP (Lỗi ghi dữ liệu vào zip: No space left on device (os error 28)). Các tệp vẫn nằm trong thư mục: /tmp/a', 'Ổ đĩa đã đầy', /dung lượng/],
    ['Đã tải 3 tệp nhưng KHÔNG nén được ZIP (Lỗi tạo mục tệp trong zip: invalid name). Các tệp vẫn nằm trong thư mục: /tmp/a', 'Không nén được tệp ZIP', /thư mục album/],
    ['Không tạo được thư mục tải: Permission denied (os error 13)', 'Không có quyền ghi vào thư mục lưu', /thư mục lưu khác/],
    ['ERROR: [instagram] xyz: HTTP Error 429: Too Many Requests', 'Máy chủ đang giới hạn lượt truy cập', /vài phút/],
  ]
  for (const [raw, title, message] of cases) {
    const res = describeError(raw)
    assert.equal(res.title, title, raw)
    assert.match(res.message, message, raw)
    assert.ok(!looksRaw(res.title) && !looksRaw(res.message), raw)
  }
})

test('câu tiếng Việt vốn dễ hiểu được giữ nguyên ý, không kèm chi tiết', () => {
  const res = describeError('Video không có phụ đề cho ngôn ngữ đã chọn — yt-dlp không ghi được tệp phụ đề nào.')
  assert.deepEqual(res, {
    title: 'Video không có phụ đề cho ngôn ngữ đã chọn',
    message: 'yt-dlp không ghi được tệp phụ đề nào.',
    detail: '',
  })

  // Câu cụ thể của app thắng gợi ý chung của nhóm "hết hạn"
  const story = describeError(
    'Không thể trích xuất Facebook Story. Story có thể đã hết hạn (quá 24 giờ) hoặc bạn không có quyền xem. ' +
      'Hãy đảm bảo bạn đã đăng nhập Facebook trên trình duyệt.'
  )
  assert.equal(story.title, 'Không thể trích xuất Facebook Story')
  assert.match(story.message, /quá 24 giờ/)

  assert.deepEqual(describeError('Tải tệp thất bại — liên kết có thể đã hết hạn hoặc bị chặn. Hãy quét lại rồi thử.'), {
    title: 'Tải tệp thất bại',
    message: 'Liên kết có thể đã hết hạn hoặc bị chặn. Hãy quét lại rồi thử.',
    detail: '',
  })

  // Đường dẫn / ngoặc kỹ thuật không lọt vào câu hiển thị
  assert.equal(
    describeError('Không tìm thấy tệp phương tiện nào từ https://www.instagram.com/someone/').title,
    'Không tìm thấy tệp phương tiện nào từ liên kết này'
  )
})

test('câu có mã thoát / định danh nội bộ không được coi là dễ hiểu', () => {
  for (const raw of ['Tải thất bại (yt-dlp kết thúc với mã 1)', 'web_scraper không tìm được stream']) {
    const res = describeError(raw, { title: 'Tải thất bại', message: 'Thử lại.' })
    assert.deepEqual(res, { title: 'Tải thất bại', message: 'Thử lại.', detail: raw })
  }
})

test('lỗi lạ dùng tiêu đề dự phòng của nơi gọi, vẫn giữ chi tiết', () => {
  const raw = 'yt-dlp extract thất bại: ERROR: [generic] Unable to extract data; please report this issue'
  const res = describeError(raw, { title: 'Không phân tích được liên kết' })
  assert.equal(res.title, 'Không phân tích được liên kết')
  assert.ok(res.message.length > 0)
  assert.equal(res.detail, raw)

  const empty = describeError(null, { title: 'Tải thất bại', message: 'Thử lại.' })
  assert.deepEqual(empty, { title: 'Tải thất bại', message: 'Thử lại.', detail: '' })
})

test('fileNameOf / shortenMiddle', () => {
  assert.equal(fileNameOf('/home/a/Downloads/video.mp4'), 'video.mp4')
  assert.equal(fileNameOf('C:\\Users\\a\\Downloads\\album.zip'), 'album.zip')
  assert.equal(fileNameOf('/home/a/Downloads/Album/'), 'Album')
  assert.equal(fileNameOf(''), '')

  assert.equal(shortenMiddle('ngắn.mp4', 20), 'ngắn.mp4')
  const long = shortenMiddle('Một tiêu đề video rất rất dài để kiểm tra rút gọn_1080p.mp4', 30)
  assert.equal(Array.from(long).length, 30)
  assert.match(long, /…/)
  assert.ok(long.endsWith('.mp4'))
})
