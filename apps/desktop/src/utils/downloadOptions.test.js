import test from 'node:test'
import assert from 'node:assert/strict'
import {
  checkTrimRange,
  containerNote,
  embedSubtitleLangs,
  formatTimecode,
  optionLimits,
  parseTimecode,
} from './downloadOptions.js'

test('mốc cắt đoạn đọc được giây, phút:giây, giờ:phút:giây', () => {
  for (const [raw, secs] of [['90', 90], ['1:30', 90], ['01:02:03', 3723], ['0:05.5', 5.5], [' 2:00 ', 120], ['90:00', 5400]]) {
    assert.equal(parseTimecode(raw), secs, raw)
  }
  assert.equal(parseTimecode(''), null)
  assert.equal(parseTimecode('   '), null)
  assert.equal(parseTimecode(undefined), null)
})

test('mốc gõ sai bị từ chối (cùng quy tắc với downloader.rs)', () => {
  for (const bad of ['1p30', '1:75', '-5', '1:2:3:4', 'abc', '1..5', '.5', '1:', '1.5:00']) {
    assert.ok(Number.isNaN(parseTimecode(bad)), bad)
  }
})

test('định dạng lại số giây để hiển thị', () => {
  assert.equal(formatTimecode(5), '0:05')
  assert.equal(formatTimecode(90), '1:30')
  assert.equal(formatTimecode(3723), '1:02:03')
  assert.equal(formatTimecode(5.5), '0:05.5')
})

test('mốc cắt hợp lệ được gửi bằng giây kèm câu tóm tắt', () => {
  const r = checkTrimRange('0:05', '0:12', '3:00')
  assert.equal(r.ok, true)
  assert.equal(r.active, true)
  assert.equal(r.startTime, '5')
  assert.equal(r.endTime, '12')
  assert.match(r.summary, /0:05 → 0:12 \(dài 0:07\)/)
  assert.match(checkTrimRange('2:00', '', '3:00').summary, /2:00 → 3:00/)
  assert.match(checkTrimRange('20', '', null).summary, /hết video/)
  assert.equal(checkTrimRange('', '0:30').startTime, '0')
})

test('mốc trống hoặc chỉ có mốc 0 là tải nguyên video', () => {
  assert.deepEqual(checkTrimRange('', ''), { ok: true, active: false })
  assert.deepEqual(checkTrimRange('00:00', ''), { ok: true, active: false })
})

test('mốc ngược / gõ sai / vượt độ dài video bị chặn trước khi tải', () => {
  assert.match(checkTrimRange('0:30', '0:10').error, /phải sau mốc bắt đầu/)
  assert.match(checkTrimRange('0:10', '0:10').error, /phải sau mốc bắt đầu/)
  assert.match(checkTrimRange('1p30', '').error, /"1p30" không hợp lệ/)
  assert.match(checkTrimRange('', '1:75').error, /kết thúc "1:75"/)
  assert.match(checkTrimRange('5:00', '', '3:00').error, /vượt quá độ dài video \(3:00\)/)
  // Thời lượng không đọc được (livestream...) thì không chặn theo độ dài
  assert.equal(checkTrimRange('5:00', '', 'LIVE').ok, true)
})

test('nhúng phụ đề: tối đa 2 phụ đề thì nhúng cả hai, nhiều hơn thì đúng ngôn ngữ đang chọn', () => {
  assert.deepEqual(embedSubtitleLangs([], 'vi'), [])
  assert.deepEqual(embedSubtitleLangs(null, ''), [])
  assert.deepEqual(embedSubtitleLangs([{ lang: 'en' }, { lang: 'vi' }], ''), ['en', 'vi'])
  const many = [{ lang: 'en' }, { lang: 'vi' }, { lang: 'ja' }]
  assert.deepEqual(embedSubtitleLangs(many, 'ja'), ['ja'])
  assert.deepEqual(embedSubtitleLangs(many, ''), ['en'])
  assert.deepEqual(embedSubtitleLangs(many, 'không-có'), ['en'])
})

test('tuỳ chọn không có tác dụng với kết quả đang mở thì báo lý do', () => {
  const youtube = { platform: 'youtube', subtitles: [{ lang: 'en' }], chapters: [{ title: 'A' }] }
  assert.deepEqual(optionLimits(youtube), { embedSubs: '', sponsorBlock: '', splitChapters: '', embedThumbnail: '' })

  const tiktok = { platform: 'tiktok', originalUrl: 'https://www.tiktok.com/@a/video/1' }
  const limits = optionLimits(tiktok)
  assert.match(limits.embedSubs, /không có phụ đề/)
  assert.match(limits.sponsorBlock, /YouTube/)
  assert.match(limits.splitChapters, /không chia chương/)

  // Engine không nhận ra nền tảng nhưng link là YouTube
  assert.equal(optionLimits({ platform: 'generic', originalUrl: 'https://youtu.be/abc' }).sponsorBlock, '')
  assert.match(optionLimits({ originalUrl: 'https://notyoutube.com.evil/x' }).sponsorBlock, /YouTube/)
})

test('đang cắt đoạn thì phụ đề / chương / SponsorBlock không áp dụng', () => {
  const youtube = { platform: 'youtube', subtitles: [{ lang: 'en' }], chapters: [{ title: 'A' }] }
  const limits = optionLimits(youtube, { trimming: true })
  for (const key of ['embedSubs', 'sponsorBlock', 'splitChapters']) {
    assert.match(limits[key], /cắt đoạn/, key)
  }
  assert.equal(limits.embedThumbnail, '')
})

test('MOV / WebM / GIF không nhúng được ảnh bìa', () => {
  for (const c of ['mov', 'webm', 'gif']) {
    assert.match(optionLimits(null, { videoContainer: c }).embedThumbnail, /không chứa được ảnh bìa/, c)
  }
  for (const c of ['auto', 'mp4', 'mkv']) {
    assert.equal(optionLimits(null, { videoContainer: c }).embedThumbnail, '', c)
  }
})

test('định dạng phải chuyển mã / GIF có lưu ý riêng', () => {
  assert.match(containerNote('webm'), /chuyển mã/)
  assert.match(containerNote('mov'), /chuyển mã/)
  assert.match(containerNote('gif'), /không có tiếng/)
  assert.equal(containerNote('mp4'), '')
  assert.equal(containerNote('auto'), '')
})
