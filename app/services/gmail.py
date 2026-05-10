"""Gmail API client for sending outreach emails, polling supplier replies,
and archiving handled threads."""

import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build

from app.services.google_auth import get_google_credentials


class GmailService:
    def __init__(self):
        creds = get_google_credentials()
        self.service = build("gmail", "v1", credentials=creds)

    def get_profile(self) -> dict:
        return self.service.users().getProfile(userId="me").execute()

    def list_unread_threads(self, max_results: int = 50) -> list[dict]:
        result = (
            self.service.users()
            .threads()
            .list(userId="me", q="is:unread is:inbox", maxResults=max_results)
            .execute()
        )
        return result.get("threads", [])

    def get_thread(self, thread_id: str) -> dict:
        return self.service.users().threads().get(userId="me", id=thread_id).execute()

    def send_email(self, to: str, subject: str, body: str) -> dict:
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return (
            self.service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )

    def reply_to_thread(self, thread_id: str, to: str, subject: str, body: str) -> dict:
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return (
            self.service.users()
            .messages()
            .send(userId="me", body={"raw": raw, "threadId": thread_id})
            .execute()
        )

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Fetch attachment bytes from Gmail API."""
        result = (
            self.service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        return base64.urlsafe_b64decode(result["data"])

    def archive_thread(self, thread_id: str) -> None:
        self.service.users().threads().modify(
            userId="me",
            id=thread_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()
