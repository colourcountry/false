#!/usr/bin/python3

import markdown, re, logging, urllib, rdflib, html
import xml.etree.ElementTree as etree

F = rdflib.Namespace("http://id.colourcountry.net/false/")

class UnescapePostprocessor(markdown.postprocessors.Postprocessor):
    def run(self, s):
        return html.unescape(s)

class GeminiTreeprocessor(markdown.treeprocessors.Treeprocessor):
    def run(self, doc):
        def el_to_text(e):
            inner_text = re.sub(r"[\r\n]+"," ",e.text or "")
            tail = re.sub(r"[\r\n]+"," ",e.tail or "")

            if e.tag.lower() == "p":
                s = "\n\n"+inner_text
            elif e.tag.lower() in ["li","ul"]:
                s = "\n"
            elif e.tag.lower() in ["h1","h2","h3","h4","h5","h6"]:
                s = "\n\n### "+inner_text
            elif e.tag.lower() == "li":
                s = "\n* "+inner_text
            elif e.tag.lower() == "code":
                if "\n" in e.text:
                    s = "\n```\n"+e.text+"\n```\n"
                else:
                    s = " ❰ "+inner_text+" ❱ "
            elif e.tag.lower() == "b":
                s = "❧"+inner_text+"☙"
            elif e.tag.lower() == "i":
                s = "⋰"+inner_text+"⋰"
            elif e.tag.lower() == "false-content":
                # Because we have element placeholders kicking around
                # it seems impossible to protect this as an actual element,
                # so invent a cruddy syntax that we can spot later
                e.tag = "false-rescued"
                s = etree.tostring(e,encoding="unicode",short_empty_elements=True)
                if e.tail:
                    s = s[:-len(e.tail)]
            else:
                s = inner_text

            for c in e:
                s += el_to_text(c)

            return s+tail

        new_content = el_to_text(doc)
        root = etree.Element(doc.tag);
        root.text = new_content;
        return root

class ImgRewriter(markdown.treeprocessors.Treeprocessor):
    def __init__(self, md, tg, base):
        self.tg = tg
        self.base = base
        super(ImgRewriter, self).__init__(md)

    def run(self, doc):

        for parent in doc.findall('.//img/..'):
            for image in parent.findall('.//img'):
                src = image.get('src')
                src = urllib.parse.urljoin(self.base, src)
                src_safe = self.tg.safePath(src)
                if src_safe in self.tg.entities:
                    logging.debug("Found image with src {src}".format(src=src))
                    image.set('src', src)
                    image.set('context', F.embed)
                    image.tag = 'false-content'
                else:
                    logging.info("removing embed of unknown or private entity {src}".format(src=src))
                    parent.remove(image)

        for parent in doc.findall('.//a/..'):
            for link in parent.findall('.//a'):
                href = link.get('href')
                href = urllib.parse.urljoin(self.base, href)

                href_safe = self.tg.safePath(href)
                if href_safe in self.tg.entities:
                    logging.debug("Found link with href {href}".format(href=href))
                    link.set('src', href)
                    link.set('context', F.link)
                    link.tag = 'false-content'
                    #FIXME think of a way to retain the link text
                    for c in link:
                        link.remove(c)
                    link.text = ''
                else:
                    logging.info("removing link to unknown or private entity {href}".format(href=href))
                    parent.remove(link)

class ImgRewriteExtension(markdown.extensions.Extension):
    def __init__(self, **kwargs):
        self.config = {'tg' : ['This has to be a string for reasons', 'The templatablegraph to query for embedded items'],
                       'base' : ['http://example.org/', 'The base URI to use when embedded content is specified as a relative URL']}
        super(ImgRewriteExtension, self).__init__(**kwargs)

    def extendMarkdown(self, md, md_globals):
        img_rw = ImgRewriter(md, self.getConfig('tg'), self.getConfig('base'))
        md.treeprocessors.register(img_rw, 'imgrewrite', 2)

class GeminiExtension(ImgRewriteExtension):
    def extendMarkdown(self, md, md_globals):
        super(GeminiExtension, self).extendMarkdown(md, md_globals)
        md.treeprocessors.register(GeminiTreeprocessor(md), 'gemini', 1)
        md.postprocessors.register(UnescapePostprocessor(md), 'unescape', 0)


def get_markdown_processor(tg,cfg):
    if cfg.page_file_type=='html':
        return markdown.Markdown(output_format="html5", extensions=[ImgRewriteExtension(tg=tg, base=cfg.id_base), 'tables'])
    elif cfg.page_file_type=='gmi':
        return markdown.Markdown(output_format="xhtml", extensions=[GeminiExtension(tg=tg, base=cfg.id_base)])
    raise ValueError(f"No markdown processor available for {cfg.page_file_type}")
