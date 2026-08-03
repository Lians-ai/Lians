import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_paid_deal_packet.py"


def base_command(output_dir: Path) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT),
        "--customer-legal-name",
        "Example Buyer, Inc.",
        "--customer-address",
        "100 Test Avenue, New York, NY 10001",
        "--billing-contact",
        "Buyer Name <buyer@example.invalid>",
        "--technical-owner",
        "Technical Owner",
        "--workflow",
        "Synthetic regulated-agent authorization decision",
        "--success-criterion",
        "Reconstruct exact decision-time evidence",
        "--start-date",
        "2026-08-10",
        "--delivery-date",
        "2026-08-21",
        "--budget-owner",
        "Budget Owner",
        "--output-dir",
        str(output_dir),
    ]


class PaidDealPacketTests(unittest.TestCase):
    def test_generates_verified_order_form_without_invoice(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            result = subprocess.run(
                base_command(output_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Invoice not generated", result.stdout)

            order_form = (
                output_dir
                / "example-buyer-inc"
                / "lians-example-buyer-inc-order-form.pdf"
            )
            invoice = (
                output_dir
                / "example-buyer-inc"
                / "lians-example-buyer-inc-kickoff-invoice.pdf"
            )
            self.assertTrue(order_form.exists())
            self.assertFalse(invoice.exists())

            fields = PdfReader(order_form).get_fields() or {}
            self.assertEqual(fields["customer_legal_name"]["/V"], "Example Buyer, Inc.")
            self.assertEqual(fields["target_start_date"]["/V"], "2026-08-10")
            self.assertEqual(fields["target_delivery_date"]["/V"], "2026-08-21")

    def test_rejects_empty_required_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            command = base_command(Path(temp))
            index = command.index("--workflow") + 1
            command[index] = "   "
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("value must not be empty", result.stderr)

    def test_rejects_delivery_before_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            command = base_command(Path(temp))
            index = command.index("--delivery-date") + 1
            command[index] = "2026-08-09"
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Delivery date must be on or after the start date",
                result.stderr,
            )

    def test_rejects_partial_invoice_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            command = base_command(Path(temp)) + [
                "--invoice-number",
                "TEST-001",
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Invoice generation requires all payment fields together",
                result.stderr,
            )

    def test_generates_invoice_with_distinct_order_form_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            command = base_command(output_dir) + [
                "--provider-address",
                "TEST ONLY - NO LEGAL ADDRESS",
                "--invoice-number",
                "TEST-NONPAYABLE-001",
                "--invoice-date",
                "2026-08-03",
                "--order-form-date",
                "2026-08-01",
                "--payment-method",
                "TEST ONLY - NO PAYMENT METHOD",
                "--payment-instructions",
                "NONPAYABLE TEST - DO NOT SEND FUNDS",
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            invoice = (
                output_dir
                / "example-buyer-inc"
                / "lians-example-buyer-inc-kickoff-invoice.pdf"
            )
            self.assertTrue(invoice.exists())
            fields = PdfReader(invoice).get_fields() or {}
            self.assertEqual(fields["invoice_date"]["/V"], "2026-08-03")
            self.assertEqual(fields["order_form_date"]["/V"], "2026-08-01")


if __name__ == "__main__":
    unittest.main()
