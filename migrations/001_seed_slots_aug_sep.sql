-- Slippy Goalz Arena — seed Available slots (Pakistan / Lahore, Asia/Karachi, UTC+5)
-- 17 Aug 2026 → 30 Sep 2026
-- Each day 12:00–23:00 PKT (noon until the last hour before midnight)
-- Hourly. Skips any Date+Time that already exists.

SET TIME ZONE 'Asia/Karachi';

INSERT INTO public.slots ("Date", "Time", "Day", "Status", "Booked By", "Phone", "Booking ID")
SELECT
  to_char(slot_at AT TIME ZONE 'Asia/Karachi', 'YYYY-MM-DD'),
  to_char(slot_at AT TIME ZONE 'Asia/Karachi', 'HH24:MI'),
  trim(to_char(slot_at AT TIME ZONE 'Asia/Karachi', 'Day')),
  'Available',
  'EMPTY',
  'EMPTY',
  ''
FROM generate_series(
  timestamptz '2026-08-17 12:00:00+05',
  timestamptz '2026-09-30 23:00:00+05',
  interval '1 hour'
) AS slot_at
WHERE EXTRACT(HOUR FROM slot_at AT TIME ZONE 'Asia/Karachi') BETWEEN 12 AND 23
ON CONFLICT ("Date", "Time") DO NOTHING;
