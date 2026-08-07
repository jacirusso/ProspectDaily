# PDF render service — WeasyPrint + its system libraries.
FROM python:3.11-slim

# WeasyPrint runtime deps (Pango/cairo/gdk-pixbuf) + clean sans fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 \
      libpangocairo-1.0-0 \
      libpangoft2-1.0-0 \
      libcairo2 \
      libgdk-pixbuf-2.0-0 \
      libffi8 \
      shared-mime-info \
      fonts-liberation \
      fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "weasyprint==62.3"

WORKDIR /app
COPY pdfservice/server.py /app/server.py

ENV PORT=8000
EXPOSE 8000
CMD ["python", "server.py"]
