import test from 'node:test'
import assert from 'node:assert/strict'
import { MAX_FINISHED_TASKS, busySourceKeys, isTaskFinished, trimFinishedTasks } from './downloadTasks.js'

const task = (id, status, sourceKey = null) => ({ id, sourceKey, progress: { status } })

test('thẻ đã xong vượt giới hạn bị bỏ, thẻ đang chạy luôn được giữ', () => {
  // Danh sách xếp mới nhất trước: 3 đang chạy xen giữa 30 thẻ đã xong
  const tasks = []
  for (let i = 0; i < 30; i++) tasks.push(task(`done-${i}`, i % 3 === 0 ? 'error' : 'completed'))
  tasks.splice(5, 0, task('run-a', 'downloading'))
  tasks.splice(17, 0, task('run-b', 'preparing'))
  tasks.push(task('run-c', 'processing'))

  const trimmed = trimFinishedTasks(tasks, 20)
  assert.equal(trimmed.filter(isTaskFinished).length, 20)
  for (const id of ['run-a', 'run-b', 'run-c']) assert.ok(trimmed.some((t) => t.id === id), id)
  // Bỏ các thẻ CŨ nhất (cuối danh sách), giữ đúng thứ tự
  assert.deepEqual(
    trimmed.filter(isTaskFinished).map((t) => t.id),
    tasks.filter(isTaskFinished).slice(0, 20).map((t) => t.id)
  )
})

test('không có gì để bỏ thì trả về chính mảng cũ (React không vẽ lại)', () => {
  const tasks = [task('a', 'completed'), task('b', 'downloading')]
  assert.equal(trimFinishedTasks(tasks), tasks)
  assert.ok(MAX_FINISHED_TASKS >= 5)
})

test('tập nguồn đang bận chỉ gồm tác vụ chưa xong', () => {
  const keys = busySourceKeys([
    task('1', 'downloading', 'item:1'),
    task('2', 'completed', 'item:2'),
    task('3', 'cancelled', 'item:3'),
    task('4', 'preparing', 'profile-batch'),
    task('5', 'processing'),
  ])
  assert.deepEqual([...keys].sort(), ['item:1', 'profile-batch'])
})
