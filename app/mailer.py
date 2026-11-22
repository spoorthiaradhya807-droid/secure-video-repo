import boto3
from flask import current_app


def _deliver(to, subject, text, dev_note, link):
    """Send via Amazon SES. Without SES_SENDER (dev mode) the link is printed in the terminal."""
    cfg = current_app.config
    if not cfg.get("SES_SENDER"):
        current_app.logger.warning("DEV MODE (no SES_SENDER). %s for %s:\n%s", dev_note, to, link)
        return False
    try:
        boto3.client("ses", region_name=cfg["SES_REGION"]).send_email(
            Source=cfg["SES_SENDER"], Destination={"ToAddresses": [to]},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": text}}})
        return True
    except Exception:  # never reveal delivery problems to the requester
        current_app.logger.exception("SES send failed for %s", to)
        return False


def send_reset_email(to, link):
    minutes = current_app.config["RESET_TOKEN_SECONDS"] // 60
    text = (f"Hello,\n\nSomeone asked to reset the password of your SecureStream account.\n"
            f"Open this link within {minutes} minutes to choose a new password:\n\n{link}\n\n"
            f"If this was not you, ignore this e-mail - your password stays unchanged.\n")
    return _deliver(to, "Reset your SecureStream password", text, "Password reset link", link)


def send_invite_email(to, link, inviter):
    text = (f"Hello,\n\n{inviter} invited you to publish videos on SecureStream as an instructor.\n"
            f"Open this link within 48 hours to create your account:\n\n{link}\n\n"
            f"The link works once. If you did not expect this invitation, ignore this e-mail.\n")
    return _deliver(to, "You are invited to SecureStream as an instructor", text, "Instructor invitation link", link)
