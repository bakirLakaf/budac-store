"""
نقاط دخول من الخارج (بلا جلسة ولا CSRF — كل واحدة عندها حماية خاصة بها):

  • /webhooks/whatsapp  ← Meta: تحقّق الاشتراك (GET) + ردود الزبون وحالات الإرسال (POST)
  • /internal/jobs/run  ← cron خارجي: يشغّل دورة الواتساب + مزامنة التتبّع
"""

import hmac
import os

from flask import Blueprint, request, jsonify, current_app, abort

from models import db
from services import whatsapp_api

webhooks_bp = Blueprint("webhooks", __name__)


@webhooks_bp.route("/webhooks/whatsapp", methods=["GET"])
def whatsapp_verify():
    """Meta تسقسي مرة وحدة عند الإعداد: hub.verify_token لازم يطابق WA_VERIFY_TOKEN."""
    expected = (os.environ.get("WA_VERIFY_TOKEN") or "").strip()
    token = request.args.get("hub.verify_token", "")
    if (expected and request.args.get("hub.mode") == "subscribe"
            and hmac.compare_digest(expected, token)):
        return request.args.get("hub.challenge", ""), 200
    abort(403)


@webhooks_bp.route("/webhooks/whatsapp", methods=["POST"])
def whatsapp_receive():
    raw = request.get_data()
    # ⚠️ التوقيع إجباري: بلاه أي واحد يقدر يبعث «CONFIRM» وهمي
    if not whatsapp_api.verify_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        abort(403)
    try:
        whatsapp_api.handle_webhook(request.get_json(silent=True) or {})
    except Exception:   # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("webhook واتساب: خطأ فالمعالجة")
        # 200 دايمًا: Meta تعاود نفس الإشعار بلا نهاية إذا رجّعنا خطأ
    return jsonify(ok=True)


@webhooks_bp.route("/internal/jobs/run", methods=["GET", "POST"])
def run_jobs():
    """
    cron خارجي (مثلاً cron-job.org كل 10 دقائق):
        https://budacdz.com/internal/jobs/run?key=<JOBS_SECRET>
    بلا JOBS_SECRET فالبيئة النقطة مغلقة (404).
    """
    secret = (os.environ.get("JOBS_SECRET") or "").strip()
    if len(secret) < 16:
        abort(404)
    if not hmac.compare_digest(secret, request.args.get("key", "")):
        abort(403)
    from services import jobs
    result = jobs.run_once(current_app._get_current_object(), force_sync=False)
    return jsonify(ok=True, whatsapp=list(result["whatsapp"]), sync=result["sync"])
