import json
import time
from io import BytesIO

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .extractor import ExtractError, extract, pick_range, pick_serial, warm_up
from .models import ScanRecord


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except ValueError:
        return {}


def _row(r):
    return {
        "id": r.id, "sl": r.sl_no,
        "serial": r.serial_number, "range": r.range_text,
        "scanned": timezone.localtime(r.created_at).strftime("%d %b %Y %H:%M"),
    }


def _duplicate(rec):
    return JsonResponse({
        "error": f"Already scanned: serial number {rec.serial_number or '(same QR code)'} is "
                 f"Sl No {rec.sl_no}. It was not added again.",
        "duplicate": True, "existing_id": rec.id,
    }, status=409)


def index(request):
    warm_up()
    return render(request, "receiving/index.html")


@require_POST
def api_scan(request):
    """Read the scanned QR link, keep only serial number + barg range, add one table row."""
    t0 = time.perf_counter()
    d = _body(request)
    url = (d.get("url") or "").strip()
    if not url:
        return JsonResponse({"error": "Nothing was scanned."}, status=400)

    rec = ScanRecord.objects.filter(url=url).first()      # same QR link seen before: no need to fetch it
    if rec:
        return _duplicate(rec)

    try:
        item = extract(url)
    except ExtractError as e:
        return JsonResponse({"error": str(e)}, status=400)

    serial = pick_serial(item["details"], item["url"], item.get("lines", []))
    if not serial:
        return JsonResponse({"error": "No serial number found on this page. Nothing was added."}, status=422)
    rec = ScanRecord.objects.filter(serial_number__iexact=serial).first()
    if rec:
        return _duplicate(rec)

    rng = pick_range(item["details"], item.get("lines", []))
    if not rng:                                # keep what the page said, so the matching can be fixed
        try:
            with open(settings.BASE_DIR / "scan_debug.txt", "w", encoding="utf-8") as fh:
                fh.write(f"URL: {item['url']}\nRendered with browser: {item.get('rendered')}\n\n")
                fh.write("--- fields ---\n" + "\n".join(f"{k}: {v}"[:300] for k, v in item["details"].items()))
                fh.write("\n\n--- page text ---\n" + "\n".join(item.get("lines", [])[:400]))
        except OSError:
            pass
    for _ in range(5):                      # retry only if two scans picked the same free number at once
        try:
            with transaction.atomic():
                rec = ScanRecord.objects.create(
                    sl_no=ScanRecord.next_sl_no(), url=item["url"], title=(item.get("title") or "")[:300],
                    details=item["details"], serial_number=serial[:100], range_text=rng,
                )
            break
        except IntegrityError:
            continue
    else:
        return JsonResponse({"error": "Could not save, please scan again."}, status=500)
    out = _row(rec)
    out["seconds"] = round(time.perf_counter() - t0, 1)
    if not rng:
        out["warning"] = "No barg range found on this page. The row was added with an empty range (details saved in scan_debug.txt)."
    return JsonResponse(out, status=201)


@require_GET
def api_records(request):
    return JsonResponse({"records": [_row(r) for r in ScanRecord.objects.all()]})


@require_POST
def api_delete(request, pk):
    get_object_or_404(ScanRecord, pk=pk).delete()
    return JsonResponse({"ok": True})


@require_POST
def api_clear(request):
    """Refresh: remove every scanned row, so the next scan starts again at Sl No 1."""
    count, _ = ScanRecord.objects.all().delete()
    return JsonResponse({"ok": True, "removed": count})


@require_GET
def export_xlsx(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Received instruments"
    ws.append(["Sl No", "Serial Number", "Range", "Scanned At"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E46")
        c.alignment = Alignment(vertical="center")
    for r in ScanRecord.objects.all():
        ws.append([r.sl_no, r.serial_number, r.range_text,
                   timezone.localtime(r.created_at).strftime("%Y-%m-%d %H:%M")])
    for i, w in enumerate([8, 24, 22, 18], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    name = "scanned_instruments.xlsx"
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp
