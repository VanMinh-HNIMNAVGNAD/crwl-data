# Tuyệt đối không dùng dữ liệu giả (No Mock Data & No Placeholders)

- **Không mock data / fake data**: Tuyệt đối không sinh dữ liệu giả lập, số liệu thống kê ảo, thẻ tag ảo, hoặc preview giả khi chưa có dữ liệu thật từ API/Database.
- **Thiết kế công cụ thực dụng (Tool Design)**: Đây là một công cụ (utility tool) trích xuất và tải media, không phải trang landing page marketing hay dashboard rườm rà. Giữ giao diện tinh gọn, sạch sẽ, căn chỉnh chuẩn xác, tập trung vào công năng chính.
- **Trải nghiệm đóng/mở chuẩn mực**: Mọi modal, drawer, dropdown đều phải:
  1. Sử dụng Portal (`createPortal` ra `document.body`) để không bị vướng stacking context / z-index / sticky header.
  2. Có nút đóng rõ ràng với nhãn chữ (ví dụ: `✕ Đóng`).
  3. Đóng ngay khi click chuột ra ngoài vùng nội dung (`pointerdown`).
  4. Đóng ngay khi bấm phím `Escape`.
