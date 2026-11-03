from email import policy
from email.parser import BytesParser
from pathlib import Path
from .web import html_text

MAIL_HEADERS=["Subject","From","To","Cc","Date"]

def read_eml(path:Path)->str:
    with open(path,"rb") as f:
        msg=BytesParser(policy=policy.default).parse(f)#it decodes 
    blocks=[]
    headers=[]
    for name in MAIL_HEADERS:
        value=msg.get(name)
        if value:
            headers.append(f"{name}: {value}")
    if headers:
        blocks.append("\n".join(headers))
    body=msg.get_body(preferencelist=("plain","html"))#it finds the best so we dont copy it twice
    if body is not None:
        content=str(body.get_content())
        if body.get_content_subtype()=="html":
            text=html_text(content)
        else:
            text=content.strip()
        if text:
            blocks.append(text)
    names=[]
    for attachment in msg.iter_attachments():#only the names
        name=attachment.get_filename()
        if name:
            names.append(name)
    if names:
        blocks.append("Attachments: "+", ".join(names))
    return "\n\n".join(blocks)
