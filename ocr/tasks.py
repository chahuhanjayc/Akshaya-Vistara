"""
ocr/tasks.py

Celery tasks for asynchronous OCR processing.

process_ocr_submission(submission_id)
    Runs Tesseract OCR on the uploaded file and updates the OCRSubmission
    record with extracted text + parsed fields.

    Status transitions:
        Pending → Processing → Pending  (success; ready for human review)
        Pending → Processing → Error    (OCR failed; user fills manually)

The view (ocr_upload) dispatches this task with .delay() and immediately
redirects the user to the verify page, which polls /ocr/<pk>/status/ via
JavaScript until OCR completes.
"""

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    name="ocr.tasks.process_ocr_submission",
)
def process_ocr_submission(self, submission_id: int) -> dict:
    """
    Celery task: run OCR on an OCRSubmission and persist the results.

    Args:
        submission_id: PK of the OCRSubmission to process.

    Returns:
        dict with keys: status ("ok" | "error"), error (str, optional)
    """
    # Import here to avoid circular imports at module load time
    from .models import OCRSubmission
    from . import ocr_utils

    try:
        submission = OCRSubmission.objects.get(pk=submission_id)
    except OCRSubmission.DoesNotExist:
        logger.error("OCRSubmission %s not found — task aborted.", submission_id)
        return {"status": "error", "error": "Submission not found"}

    # Mark as processing so the UI can show the spinner
    submission.status = OCRSubmission.STATUS_PROCESSING
    submission.save(update_fields=["status", "updated_at"])

    try:
        ocr_utils.process_submission(submission)

        # If we reach here, OCR succeeded — set back to Pending (ready for review)
        submission.status = OCRSubmission.STATUS_PENDING
        submission.save(update_fields=["status", "updated_at"])

        logger.info("OCR complete for submission %s", submission_id)
        return {"status": "ok"}

    except Exception as exc:
        logger.exception("OCR failed for submission %s: %s", submission_id, exc)

        # Retry up to max_retries times before giving up
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            submission.status    = OCRSubmission.STATUS_ERROR
            submission.ocr_error = str(exc)
            submission.save(update_fields=["status", "ocr_error", "updated_at"])
            return {"status": "error", "error": str(exc)}
