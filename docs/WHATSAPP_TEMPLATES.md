# Slippy Goalz Arena — WhatsApp templates (Meta Business Manager)

Create / keep these **Approved** templates. Body variables must match app/wa_copy.py:

## booking_confirmed  (or set WHATSAPP_CONFIRM_TEMPLATE)
Language: en_US (or your approved locale)

Body example:
Hi {{1}}, your Slippy Goalz Arena pitch is confirmed for {{2}} at {{3}}. Arrive 5 mins early. See you on the pitch!

Vars: {{1}}=name  {{2}}=date  {{3}}=time

## appointment_reminder_24h
Body example:
Reminder {{1}}: Slippy Goalz Arena tomorrow {{2}} at {{3}}. Arrive 5 mins early.

## appointment_reminder_urgent
Body example:
{{1}}, your Slippy Goalz Arena session is today at {{3}} ({{2}}). See you soon!

After Approved:
1. Set WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID on Railway
2. Set REMINDERS_ENABLED=true for the cron job
3. Confirm a test booking → customer receives WhatsApp
