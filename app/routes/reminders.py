from html import escape
import os
from datetime import datetime, timedelta
import resend
from apscheduler.schedulers.background import BackgroundScheduler

resend.api_key = os.environ.get("RESEND_API_KEY")
scheduler = BackgroundScheduler()
scheduler.start()


def send_email(to_email: str, subject: str, html_body: str):
    try:
        resend.Emails.send({
            "from": "bookings@yourdomain.com",  # swap for your verified domain, or use Resend's test sender to start
            "to": to_email,
            "subject": subject,
            "html": html_body,
        })
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False


def send_booking_confirmation(customer_email: str, business_name: str, service: str, appointment_time: datetime):
    subject = f"Appointment Confirmed - {business_name}"
    html = f"""
    <p>Hi! Your <strong>{escape(str(service))}</strong> appointment at <strong>{escape(str(business_name))}</strong> is confirmed for:</p>
    <p><strong>{escape(appointment_time.strftime('%A, %b %d at %I:%M %p'))}</strong></p>
    <p>Reply to this email if you need to reschedule.</p>
    """
    send_email(customer_email, subject, html)


def schedule_reminder(customer_email: str, business_name: str, service: str, appointment_time: datetime):
    reminder_time = appointment_time - timedelta(hours=24)

    # don't schedule if appointment is already less than 24hrs away
    if reminder_time <= datetime.now():
        return

    subject = f"Reminder: Your appointment tomorrow - {business_name}"
    html = f"""
    <p>Just a reminder — your <strong>{escape(str(service))}</strong> appointment at <strong>{escape(str(business_name))}</strong> is tomorrow at <strong>{escape(appointment_time.strftime('%I:%M %p'))}</strong>.</p>
    <p>See you then!</p>
    """
    scheduler.add_job(
        send_email,
        'date',
        run_date=reminder_time,
        args=[customer_email, subject, html]
    )
