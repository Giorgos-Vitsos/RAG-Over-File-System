from email.message import EmailMessage

import pytest

from test_parsers import parse_one

# eml (D98): the headers people search by, the text, and the names of the attachments

HEAD = "Subject: Εξεταστική\nFrom: Μαρία <maria@example.com>\nTo: giorgos@example.com\nDate: Tue, 03 Nov 2026 10:00:00 +0200"
BODY = "Καλησπέρα,\nη εξέταση είναι την Παρασκευή."


def message(body: str | None = BODY, subtype="plain", charset="utf-8", cte=None, **headers):
    msg = EmailMessage()
    msg["Subject"] = headers.get("subject", "Εξεταστική")
    msg["From"] = headers.get("sender", "Μαρία <maria@example.com>")
    msg["To"] = headers.get("to", "giorgos@example.com")
    if "cc" in headers:
        msg["Cc"] = headers["cc"]
    msg["Date"] = "Tue, 03 Nov 2026 10:00:00 +0200"
    if body is not None:
        if cte:
            msg.set_content(body, subtype=subtype, charset=charset, cte=cte)
        else:
            msg.set_content(body, subtype=subtype, charset=charset)
    return msg


def eml(tmp_path, msg):
    return parse_one(tmp_path, "mail.eml", msg.as_bytes())


def test_read_eml_plain(tmp_path):
    doc = eml(tmp_path, message())
    assert (doc.parse_status, doc.content_type, doc.raw_text) == ("ok", "prose", HEAD + "\n\n" + BODY)


# a Greek subject is written encoded in the file (=?utf-8?b?...?=); it is read as Greek
def test_read_eml_encoded_subject(tmp_path):
    raw = message().as_bytes()
    assert "Εξεταστική".encode("utf-8") not in raw
    assert eml(tmp_path, message()).raw_text.startswith("Subject: Εξεταστική\n")


# the text in an old Greek charset, or quoted-printable
@pytest.mark.parametrize("charset, cte", [("iso-8859-7", None), ("utf-8", "quoted-printable"), ("windows-1253", "base64")])
def test_read_eml_charsets(tmp_path, charset, cte):
    assert eml(tmp_path, message(charset=charset, cte=cte)).raw_text.endswith(BODY)


# only html: read like an html file
def test_read_eml_html(tmp_path):
    msg = message("<h1>Ανακοίνωση</h1><p>Η εξέταση <b>μετατίθεται</b>.</p>", subtype="html")
    assert eml(tmp_path, msg).raw_text == HEAD + "\n\n# Ανακοίνωση\n\nΗ εξέταση μετατίθεται."


# text and html of the same message: the text once, not twice, and the plain text (the html is the same message
# dressed up, here with a footer the plain text does not have)
def test_read_eml_text_and_html(tmp_path):
    msg = message()
    msg.add_alternative("<p>" + BODY.replace("\n", "<br>") + "</p><p>Στάλθηκε από το κινητό</p>", subtype="html")
    assert eml(tmp_path, msg).raw_text == HEAD + "\n\n" + BODY


# attachments: their names at the end, their content is not read
def test_read_eml_attachments(tmp_path):
    msg = message()
    msg.add_attachment(b"%PDF-1.4 secret content", maintype="application", subtype="pdf", filename="σημειώσεις.pdf")
    msg.add_attachment(b"x,y", maintype="text", subtype="csv", filename="βαθμοί.csv")
    text = eml(tmp_path, msg).raw_text
    assert text == HEAD + "\n\n" + BODY + "\n\nAttachments: σημειώσεις.pdf, βαθμοί.csv"
    assert "secret" not in text


# Cc is a header people search by too; headers that are missing are not written
def test_read_eml_headers(tmp_path):
    msg = message(cc="nikos@example.com")
    del msg["To"]
    text = eml(tmp_path, msg).raw_text
    assert text.split("\n\n")[0] == ("Subject: Εξεταστική\nFrom: Μαρία <maria@example.com>\nCc: nikos@example.com\n"
                                     "Date: Tue, 03 Nov 2026 10:00:00 +0200")


# a message without a body still has its headers
def test_read_eml_without_body(tmp_path):
    doc = eml(tmp_path, message(body=None))
    assert (doc.parse_status, doc.raw_text) == ("ok", HEAD)
