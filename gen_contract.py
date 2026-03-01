"""Generate a sample SaaS contract PDF with intentionally vendor-favorable clauses."""
from fpdf import FPDF

pdf = FPDF()
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_page()

pdf.set_font("Helvetica", "B", 14)
pdf.cell(w=0, h=10, text="SOFTWARE AS A SERVICE AGREEMENT", new_x="LMARGIN", new_y="NEXT", align="C")
pdf.ln(5)

sections = {
    "PARTIES": (
        'This Software as a Service Agreement (the "Agreement") is entered into '
        "as of January 15, 2026 by and between CloudTech Solutions Inc., a Delaware "
        'corporation ("Provider"), and Acme Manufacturing Corp., a California '
        'corporation ("Customer").'
    ),
    "1. DEFINITIONS": (
        '1.1 "Service" means the cloud-based ERP software platform.\n'
        '1.2 "Customer Data" means all data submitted by Customer to the Service.\n'
        '1.3 "Authorized Users" means employees and contractors authorized to use the Service.'
    ),
    "2. SERVICE AND LICENSE": (
        "2.1 Provider grants Customer a non-exclusive, non-transferable right to access "
        "and use the Service during the Term for internal business purposes.\n"
        "2.2 Customer shall not sublicense, sell, reverse engineer, or use the Service "
        "to develop a competing product.\n"
        "2.3 Provider shall maintain 99.5% uptime measured monthly."
    ),
    "3. FEES AND PAYMENT": (
        "3.1 Customer shall pay 240,000 USD per year in quarterly installments.\n"
        "3.2 Overdue amounts accrue interest at 1.5% per month.\n"
        "3.3 Fees are exclusive of taxes."
    ),
    "4. TERM AND TERMINATION": (
        "4.1 Three (3) year initial term from the Effective Date.\n"
        "4.2 Auto-renews for one (1) year periods with thirty (30) days notice to cancel.\n"
        "4.3 Either Party may terminate for material breach with fifteen (15) days cure period.\n"
        "4.4 Upon termination, Provider makes Customer Data available for seven (7) days, "
        "then may delete all Customer Data."
    ),
    "5. INTELLECTUAL PROPERTY": (
        "5.1 Provider retains all rights in the Service including modifications developed "
        "using Customer feedback.\n"
        "5.2 Customer retains rights in Customer Data but grants Provider an irrevocable, "
        "perpetual, worldwide license to use Customer Data in anonymized form for any purpose.\n"
        "5.3 All feedback and suggestions become sole property of Provider."
    ),
    "6. DATA PROTECTION": (
        "6.1 Provider processes Customer Data solely for providing the Service.\n"
        "6.2 Provider implements commercially reasonable security measures.\n"
        "6.3 Breach notification within seventy-two (72) hours.\n"
        "6.4 Customer Data may be stored in any country where Provider maintains facilities."
    ),
    "7. LIMITATION OF LIABILITY": (
        "7.1 TOTAL LIABILITY CAPPED AT FEES PAID IN THE SIX (6) MONTHS PRECEDING THE CLAIM.\n"
        "7.2 NO LIABILITY FOR INDIRECT, CONSEQUENTIAL, OR PUNITIVE DAMAGES.\n"
        "7.3 Provider not liable for damages from use in violation of this Agreement."
    ),
    "8. INDEMNIFICATION": (
        "8.1 Provider indemnifies Customer against IP infringement claims only, with "
        "sole control of defense.\n"
        "8.2 Customer indemnifies Provider and its officers, directors, employees against "
        "ALL claims arising from use of the Service, Customer Data, breach, or Authorized Users."
    ),
    "9. GOVERNING LAW": (
        "9.1 Governed by Delaware law.\n"
        "9.2 Binding arbitration in Wilmington, Delaware. JURY TRIAL WAIVED.\n"
        "9.3 Prevailing party recovers attorneys fees."
    ),
    "10. GENERAL": (
        "10.1 Entire agreement, supersedes all prior.\n"
        "10.2 Amendments require writing signed by both Parties.\n"
        "10.3 Customer may not assign without Provider consent (sole discretion)."
    ),
}

for title, body in sections.items():
    pdf.set_font("Helvetica", "B", 10)
    pdf.ln(3)
    pdf.cell(w=0, h=6, text=title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(w=0, h=4.5, text=body)

# Signature block
pdf.ln(10)
pdf.set_font("Helvetica", "", 9)
pdf.multi_cell(w=0, h=5, text=(
    "IN WITNESS WHEREOF, the Parties have executed this Agreement.\n\n"
    "Provider: CloudTech Solutions Inc.\n"
    "By: _______________ John Smith, VP Sales\n\n"
    "Customer: Acme Manufacturing Corp.\n"
    "By: _______________ Jane Doe, General Counsel"
))

pdf.output("/Users/richa/Downloads/files/sample-contract.pdf")
print("PDF created: sample-contract.pdf")
