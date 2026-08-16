"""
email_notify.py — sends the weekly report by email via SMTP.
Works with Gmail (use an App Password, not your real password), Outlook, or any SMTP provider.
All credentials are read from environment variables — never hardcode them.
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_report_email(subject, text_body, html_body=None):
    smtp_host = os.environ["SMTP_HOST"]         # e.g. smtp.gmail.com
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ["SMTP_USER"]          # your sending email address
    smtp_pass = os.environ["SMTP_PASS"]          # app password
    to_addr = os.environ.get("EMAIL_TO", smtp_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_addr], msg.as_string())
    print(f"Email sent to {to_addr}")
