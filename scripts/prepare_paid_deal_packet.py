import argparse
import re
from datetime import date
from pathlib import Path

from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parents[1]
ORDER_TEMPLATE = ROOT / "output" / "pdf" / "lians-paid-design-partnership-order-form.pdf"
INVOICE_TEMPLATE = ROOT / "output" / "pdf" / "lians-kickoff-invoice-template.pdf"


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("Customer name does not produce a safe output slug")
    return slug


def nonempty(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise argparse.ArgumentTypeError("value must not be empty")
    return cleaned


def iso_date(value: str) -> str:
    cleaned = nonempty(value)
    try:
        date.fromisoformat(cleaned)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "date must use YYYY-MM-DD format"
        ) from exc
    return cleaned


def fill_and_verify(template: Path, output: Path, values: dict[str, str]) -> None:
    reader = PdfReader(template)
    template_fields = reader.get_fields() or {}
    missing = sorted(set(values) - set(template_fields))
    if missing:
        raise ValueError(f"Fields missing from {template.name}: {missing}")

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.update_page_form_field_values(
        None,
        values,
        auto_regenerate=False,
        flatten=False,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        writer.write(stream)

    check = PdfReader(output)
    canonical = check.get_fields() or {}
    for name, expected in values.items():
        actual = canonical.get(name, {}).get("/V")
        if actual != expected:
            raise ValueError(f"{output.name}: canonical value mismatch for {name!r}")

    seen: set[str] = set()
    for page in check.pages:
        for ref in page.get("/Annots") or []:
            widget = ref.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            parent_ref = widget.get("/Parent")
            field = parent_ref.get_object() if parent_ref else widget
            name = field.get("/T")
            if name not in values:
                continue
            seen.add(name)
            effective_value = (
                widget.get("/V") if widget.get("/V") is not None else field.get("/V")
            )
            if effective_value != values[name]:
                raise ValueError(f"{output.name}: widget value mismatch for {name!r}")
            appearance = widget.get("/AP")
            if not appearance or not appearance.get("/N"):
                raise ValueError(f"{output.name}: missing appearance for {name!r}")

    unseen = sorted(set(values) - seen)
    if unseen:
        raise ValueError(f"{output.name}: no widget found for fields {unseen}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare verified, still-interactive Lians order-form and invoice PDFs."
    )
    parser.add_argument("--customer-legal-name", required=True, type=nonempty)
    parser.add_argument("--customer-address", required=True, type=nonempty)
    parser.add_argument("--billing-contact", required=True, type=nonempty)
    parser.add_argument("--technical-owner", required=True, type=nonempty)
    parser.add_argument("--workflow", required=True, type=nonempty)
    parser.add_argument("--success-criterion", required=True, type=nonempty)
    parser.add_argument("--start-date", required=True, type=iso_date)
    parser.add_argument("--delivery-date", required=True, type=iso_date)
    parser.add_argument("--purchase-order", default="NOT REQUIRED", type=nonempty)
    parser.add_argument("--budget-owner", required=True, type=nonempty)
    parser.add_argument("--lians-signer-name", default="Ethan Beirne", type=nonempty)
    parser.add_argument("--lians-title", default="Co-Founder and CEO", type=nonempty)
    parser.add_argument("--provider-address", type=nonempty)
    parser.add_argument("--invoice-number", type=nonempty)
    parser.add_argument("--invoice-date", type=iso_date)
    parser.add_argument("--order-form-date", type=iso_date)
    parser.add_argument("--payment-method", type=nonempty)
    parser.add_argument("--payment-instructions", type=nonempty)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "output" / "pdf" / "deals",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if date.fromisoformat(args.delivery_date) < date.fromisoformat(args.start_date):
        raise ValueError("Delivery date must be on or after the start date")

    slug = safe_slug(args.customer_legal_name)
    customer_dir = args.output_dir.resolve() / slug

    order_values = {
        "customer_legal_name": args.customer_legal_name,
        "customer_address": args.customer_address,
        "billing_contact": args.billing_contact,
        "technical_owner": args.technical_owner,
        "workflow": args.workflow,
        "success_criterion": args.success_criterion,
        "target_start_date": args.start_date,
        "target_delivery_date": args.delivery_date,
        "purchase_order": args.purchase_order,
        "budget_owner": args.budget_owner,
        "lians_signer_name": args.lians_signer_name,
        "lians_title": args.lians_title,
    }
    order_output = customer_dir / f"lians-{slug}-order-form.pdf"
    fill_and_verify(ORDER_TEMPLATE, order_output, order_values)
    print(order_output)

    invoice_inputs = {
        "provider_address": args.provider_address,
        "invoice_number": args.invoice_number,
        "invoice_date": args.invoice_date,
        "order_form_date": args.order_form_date,
        "payment_method": args.payment_method,
        "payment_instructions": args.payment_instructions,
    }
    supplied = [name for name, value in invoice_inputs.items() if value]
    if not supplied:
        print("Invoice not generated: authorized payment and provider details were not supplied.")
        return

    missing_invoice = [name for name, value in invoice_inputs.items() if not value]
    if missing_invoice:
        raise ValueError(
            "Invoice generation requires all payment fields together; missing "
            + ", ".join(missing_invoice)
        )

    invoice_values = {
        "invoice_number": args.invoice_number,
        "invoice_date": args.invoice_date,
        "provider_address": args.provider_address,
        "customer_legal_name": args.customer_legal_name,
        "billing_contact": args.billing_contact,
        "billing_address": args.customer_address,
        "purchase_order": args.purchase_order,
        "order_form_date": args.order_form_date,
        "payment_method": args.payment_method,
        "payment_instructions": args.payment_instructions,
    }
    invoice_output = customer_dir / f"lians-{slug}-kickoff-invoice.pdf"
    fill_and_verify(INVOICE_TEMPLATE, invoice_output, invoice_values)
    print(invoice_output)


if __name__ == "__main__":
    main()
