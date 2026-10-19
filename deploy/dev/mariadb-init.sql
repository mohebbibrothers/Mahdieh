-- اجازهٔ ساخت پایگاه‌دادهٔ آزمون برای کاربر توسعه (فقط محیط محلی/CI).
-- جنگو پایگاه‌دادهٔ test_mahdieh (و با xdist: test_mahdieh_gw0…) را می‌سازد و حذف می‌کند.
GRANT ALL PRIVILEGES ON `test\_mahdieh%`.* TO 'mahdieh'@'%';
GRANT ALL PRIVILEGES ON `mahdieh\_drill`.* TO 'mahdieh'@'%';
FLUSH PRIVILEGES;
