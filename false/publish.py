#!/usr/bin/python3

import rdflib
import sys, logging, os, re, urllib.parse
import ipfsapi
import jinja2, markdown
import pprint
from false.graph import *

EXTERNAL_LINKS = {
  "http://www.wikidata.org/wiki/\\1": re.compile("http://www.wikidata.org/entity/(.*)")
}

class ImgRewriter(markdown.treeprocessors.Treeprocessor):
    def __init__(self, md, tg):
        self.tg = tg
        super(ImgRewriter, self).__init__(md)

    def run(self, doc):
        "Find all images and append to markdown.images. "
        for image in doc.findall('.//img'):
            src = image.get('src')
            src_safe = self.tg.safePath(src)
            logging.debug("Found image with src %s" % src)
            if src_safe in self.tg.entities:
                if self.tg.entities[src_safe].f_url:
                    image.set('src', self.tg.entities[src_safe].f_url)
                    image.tag = 'false-embed'
                    logging.debug("Found URL %s" % self.tg.entities[src_safe].f_url)
                else:
                    pass # sometimes an image is just an image

class ImgRewriteExtension(markdown.extensions.Extension):
    def __init__(self, **kwargs):
        self.config = {'tg' : ['This has to be a string for reasons', 'The templatablegraph to query for embedded items']}
        super(ImgRewriteExtension, self).__init__(**kwargs)

    def extendMarkdown(self, md, md_globals):
        img_rw = ImgRewriter(md, self.getConfig('tg'))
        md.treeprocessors.add('imgrewrite', img_rw, '>inline')

class EmbedNotReady(Exception):
    pass

F = rdflib.Namespace("http://www.colourcountry.net/false/model/")

def get_page_path(e, ctx, e_type, output_dir, file_type='html'):
    return os.path.join(output_dir, e_type.safe, ctx, e+'.'+file_type)

def get_page_url(e, ctx, e_type, url_base, file_type='html'):
    return '%s/%s/%s/%s' % (url_base, e_type.safe, ctx, e+'.'+file_type)

def resolve_embed(m, tg, e_safe, in_p=False):
    logging.debug("Resolving embed %s" % (m.group(1)))
    attrs = {}
    for mm in re.finditer('(\S+)="([^"]*)"', m.group(1)):
        attrs[mm.group(1)]=mm.group(2)

    src = urllib.parse.urljoin(tg.entities[e_safe].id, attrs['src'])
    ref_safe = tg.safePath(src)
    if ref_safe in tg.entities:
        logging.debug("Trying to embed known entity %s" % src)
        if tg.entities[ref_safe].f_html:
            logging.debug("Success!")
            return str(tg.entities[ref_safe].f_html)
        else:
            logging.info("Couldn't embed, html is not ready")
            raise EmbedNotReady

    logging.debug("Don't know about img %s" % src)
    return m.group(0)

def get_html_body_for_rendition(tg, e, r, ipfs_client, markdown_processor):
    mt = r.f_mediaType

    if r.f_charset:
        enc = r.f_charset.pop()
    else:
        logging.warning("%s: no charset for %s rendition" % (e, mt))
        enc = 'utf-8'

    logging.info('%s: found %s rendition' % (e, mt))

    if rdflib.Literal('text/html') in mt:
        return ipfs_client.cat(r.id).decode(enc)

    if rdflib.Literal('text/markdown') in mt:
        return markdown_processor.convert(ipfs_client.cat(r.id).decode(enc))

    logging.debug("%s: media type %s is not a suitable body" % (e, mt))
    return None

def get_html_body(tg, e, ipfs_client, markdown_processor):
    available = e.f_rendition

    for r in available:
        eh = get_html_body_for_rendition(tg, e, r, ipfs_client, markdown_processor)
        if eh:
            return eh

    logging.debug("%s: no suitable body" % e)
    return '<!-- non-renderable item %s -->' % e

def publish(g, template_dir, output_dir, url_base, ipfs_client):
    tg = TemplatableGraph(g)

    jinja_e = jinja2.Environment(loader=jinja2.FileSystemLoader('templates'))

    markdown_processor = markdown.Markdown(output_format="html5", extensions=[ImgRewriteExtension(tg=tg)])

    embed_html = {}
    stage = {}
    to_write = set()

    for e_safe, e in tg.entities.items():
        stage[e] = {}

        has_url = False
        for match in e.skos_exactMatch:
            for repl, pattern in EXTERNAL_LINKS.items():
                logging.warn(match)
                if pattern.match(str(match)):
                    url = pattern.sub(repl, str(match))
                    tg.add(e.id, F.url, rdflib.Literal(url))
                    has_url = True

        if has_url:
            continue # don't need to render

        for ctx in (F.asPage, F.asEmbed): # TODO enumerate contexts that are in use

            ctx_safe = tg.safePath(ctx)
            # use the most direct type because we need to go up in a specific order
            # FIXME: provide ordered walk functions on entities?
            e_types = e.type()

            while e_types:
                for e_type in e_types:
                    try:
                        t = jinja_e.get_template(e_type.safe+'.'+ctx_safe)
                    except IOError as err:
                        logging.debug("%s: no template %s.%s" % (e, e_type.safe, ctx_safe))
                        continue

                    dest = get_page_path(e_safe, ctx_safe, e_type, output_dir)
                    url = get_page_url(e_safe, ctx_safe, e_type, url_base)

                    logging.info('%s: will render for %s as %s -> %s' % (e, ctx, e_type, dest))
                    stage[e][ctx] = (t, dest)
                    to_write.add(dest)

                    e_types = None # found a renderable type

                    # add the computed URL of the item as a full page, for templates to pick up
                    if ctx == F.asPage:
                        tg.add(e.id, F.url, rdflib.Literal(url))
                    break

                if e_types is not None:
                    # get the next layer of types
                    e_types = e_types.rdfs_subClassOf

    #for e in tg.entities:
    #    eh = get_embed_html(tg, e, ipfs_client, markdown_processor)
    #    if eh:
    #        embed_html[e] = eh

    #while len(embed_html) > 0:
    #    progress = set()
    #    for e,html in embed_html.items():
    #        try:
    #            html = re.sub("<p>\s*<false-embed([^>]*)>\s*</p>", lambda m: resolve_embed(m, tg, e, True), html)
    #            html = re.sub("<false-embed([^>]*)>", lambda m: resolve_embed(m, tg, e, False), html)
    #            logging.debug("%s: all embeds resolved" % tg.entities[e])
    #            tg.add(tg.entities[e].id, F.html, html)
    #            progress.add(e)
    #        except EmbedNotReady:
    #            continue

    #    if len(progress) == 0:
    #        raise RuntimeError("Embed loop in %s" % ','.join(embed_html.keys()))

    #    for e in progress:
    #        del(embed_html[e])

    logging.info("Stage is ready")

    progress = True
    while progress:
        progress = False
        logging.info("%s pages left to render" % len(to_write))
        for e, dests in stage.items():
            for ctx in dests:
                t, dest = dests[ctx]

                for embed in e.f_includes:
                    if F.asEmbed in stage[embed]:
                        et, ed = stage[embed][F.asEmbed]
                    else:
                        raise ValueError("%s: need %s but there is no way to embed it" % (e, embed))
                    if ed in to_write:
                        logging.info("%s: can't write %s yet, need %s" % (e, dest, ed))
                        break
                    logging.debug("%s: %s looks good" % (e, ed))
                else:
                    body = get_html_body(tg, e, ipfs_client, markdown_processor)
                    tg.add(e.id, F.html, rdflib.Literal(body))

                    content = t.render(e.po)

                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    logging.info("%s: writing %s" % (e, dest))
                    with open(dest,'w') as f:
                        f.write(content)

                    if dest in to_write:
                        to_write.remove(dest)
                        progress = True

    if to_write:
        logging.error("Embed loop, can't render these: %s" % '\n'.join(to_write))
    else:
        logging.info("All written successfully.")

if __name__=="__main__":
    g = rdflib.Graph()
    for path, dirs, files in os.walk(FALSE_SRC):
      for f in files:
          if f.endswith('.ttl'):
              logging.info("Loading %s from %s" % (f,path))
              g.load(os.path.join(path,f), format='ttl')
    publish(build_graph(g))
