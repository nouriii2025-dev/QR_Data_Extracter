from django.db import models


class ScanRecord(models.Model):
    """One scanned instrument: serial number and barg range."""
    sl_no = models.PositiveIntegerField(unique=True)
    serial_number = models.CharField(max_length=100, blank=True, default="", db_index=True)
    range_text = models.CharField(max_length=100, blank=True, default="")
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=300, blank=True)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sl_no"]

    def __str__(self):
        return f"{self.serial_number} / {self.range_text}"

    @staticmethod
    def next_sl_no():
        """Smallest unused Sl No, so a removed number is given to the next scan."""
        used = set(ScanRecord.objects.values_list("sl_no", flat=True))
        n = 1
        while n in used:
            n += 1
        return n
