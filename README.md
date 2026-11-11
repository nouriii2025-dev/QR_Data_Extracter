# Goods Receiving: scan instrument QR, list serial number + range, export

## Run it
    pip install -r requirements.txt
    python manage.py makemigrations receiving
    python manage.py migrate
    python manage.py runserver 0.0.0.0:8000
Open http://localhost:8000

## What it does
Scan the QR code. Only two things are read from the product page and added to the table:
the **serial number** and the **range in barg** (the number before `barg`; a single number such as `700 barg` is shown as `0-700 barg`, and `-600 barg` as `-600 to 0 barg`).
Each row gets a unique Sl No. Scanning an instrument again shows an "already scanned" message and adds nothing.
"Refresh" clears the whole list (after a confirmation) so a new delivery starts at Sl No 1. "Export to Excel" downloads one sheet with every row (Sl No, Serial Number, Range, Scanned At).

## Scanner
Any USB or Bluetooth barcode scanner in keyboard mode works. It types the QR content
and presses Enter; the page reads that as "scan finished". Click the scan box first.

## Speed
The app keeps one background browser open (after the first page load) and reuses the connection, so a scan takes
about a second or less. Install the optional renderer below to get this on JavaScript-built pages like the WIKA product pass;
`lxml` (in requirements.txt) speeds up parsing. After each scan the status line shows how many seconds it took.

## Pages that build content with JavaScript
If a site shows little in plain HTML, install the optional renderer once:
    pip install playwright
    playwright install chromium
The app switches to it automatically when fewer than 4 fields are found.

## If a range is empty
The row is still added, with a warning, and the page text the app saw is written to `scan_debug.txt` in the project folder.
Send that file to whoever maintains the app so the matching can be adjusted.
