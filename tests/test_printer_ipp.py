import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from hermes_pbx_bridge.printer_ipp import (
    PrintSettings,
    build_get_printer_attributes,
    build_print_job,
    create_http_connect_connection,
    parse_ipp_response,
    print_confirmation_token,
    validate_pdf,
)


class PrinterIppTests(unittest.TestCase):
    def test_get_printer_attributes_uses_required_operation_attributes(self):
        request = build_get_printer_attributes(
            request_id=7,
            printer_uri="ipp://printer.agent.lan:10631/ipp/print",
        )

        self.assertEqual(request[:8], b"\x02\x00\x00\x0b\x00\x00\x00\x07")
        self.assertIn(b"attributes-charset", request)
        self.assertIn(b"attributes-natural-language", request)
        self.assertIn(b"printer-uri", request)
        self.assertTrue(request.endswith(b"\x03"))

    def test_print_job_contains_explicit_safe_settings_and_pdf_payload(self):
        pdf = b"%PDF-1.7\nfixture\n%%EOF\n"
        settings = PrintSettings(
            copies=1,
            sides="two-sided-long-edge",
            color_mode="monochrome",
            media="na_letter_8.5x11in",
        )

        request = build_print_job(
            request_id=9,
            printer_uri="ipp://printer.agent.lan:10631/ipp/print",
            job_name="Approved test",
            document=pdf,
            settings=settings,
        )

        self.assertEqual(request[:8], b"\x02\x00\x00\x02\x00\x00\x00\x09")
        self.assertIn(b"application/pdf", request)
        self.assertIn(b"two-sided-long-edge", request)
        self.assertIn(b"monochrome", request)
        self.assertIn(b"na_letter_8.5x11in", request)
        self.assertTrue(request.endswith(pdf))

    def test_validate_pdf_rejects_non_pdf(self):
        with self.assertRaisesRegex(ValueError, "PDF"):
            validate_pdf(b"plain text")

    def test_confirmation_token_is_bound_to_document_and_settings(self):
        document = b"%PDF-1.7\nfixture\n%%EOF\n"
        settings = PrintSettings(1, "one-sided", "monochrome", "na_letter_8.5x11in")

        first = print_confirmation_token(document, settings)
        second = print_confirmation_token(document + b"changed", settings)

        self.assertRegex(first, r"^PRINT-[0-9a-f]{16}$")
        self.assertNotEqual(first, second)

    def test_parser_returns_status_and_job_identifiers(self):
        body = (
            b"\x02\x00\x00\x00\x00\x00\x00\x09"
            b"\x02"
            b"\x21\x00\x06job-id\x00\x04\x00\x00\x00\x2a"
            b"\x45\x00\x07job-uri\x00\x15ipp://printer/jobs/42"
            b"\x03"
        )

        parsed = parse_ipp_response(body)

        self.assertEqual(parsed["status_code"], 0)
        self.assertEqual(parsed["request_id"], 9)
        self.assertEqual(parsed["attributes"]["job-id"], 42)
        self.assertEqual(parsed["attributes"]["job-uri"], "ipp://printer/jobs/42")

    def test_http_connect_opens_tunnel_without_resolving_printer_name(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        received = io.BytesIO()

        def proxy():
            connection, _ = listener.accept()
            with connection:
                while b"\r\n\r\n" not in received.getvalue():
                    received.write(connection.recv(4096))
                connection.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                received.write(connection.recv(4))

        worker = threading.Thread(target=proxy)
        worker.start()
        try:
            sock = create_http_connect_connection(
                ("printer.agent.lan", 10631),
                timeout=2,
                proxy_address=listener.getsockname(),
            )
            sock.sendall(b"IPP!")
            sock.close()
        finally:
            worker.join(timeout=2)
            listener.close()

        self.assertIn(b"CONNECT printer.agent.lan:10631 HTTP/1.1", received.getvalue())
        self.assertTrue(received.getvalue().endswith(b"IPP!"))


if __name__ == "__main__":
    unittest.main()
