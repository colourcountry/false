#!/usr/bin/python3

import rdflib
import sys, logging, os, re, urllib.parse
import jinja2, markdown
import pprint
from false.graph import *

EXTERNAL_LINKS = {
  "http://www.wikidata.org/wiki/\\1": re.compile("http://www.wikidata.org/entity/(.*)")
}

F = rdflib.Namespace("http://id.colourcountry.net/false/")

HTML_FOR_CONTEXT = { F.teaser: F.asTeaser, F.embed: F.asEmbed, F.page: F.asPage }

class PublishError(ValueError):
    pass

class ImgRewriter(markdown.treeprocessors.Treeprocessor):
    def __init__(self, md, tg, base):
        self.tg = tg
        self.base = base
        super(ImgRewriter, self).__init__(md)

    def run(self, doc):
        "Find all images and append to markdown.images. "
        for image in doc.findall('.//img'):
            src = urllib.parse.urljoin(self.base, image.get('src'))
            src_safe = self.tg.safePath(src)
            logging.debug("Found image with src %s" % src)
            if src_safe in self.tg.entities:
                if self.tg.entities[src_safe].embedPath:
                    image.set('src', self.tg.entities[src_safe].embedPath)
                    image.set('context', F.embed)
                    image.tag = 'false-embed'
                else:
                    raise PublishError("don't have an embeddable version of %s" % src)
            else:
                pass # sometimes an image is just an image

        for link in doc.findall('.//a'):
            href = urllib.parse.urljoin(self.base, link.get('href'))
            href_safe = self.tg.safePath(href)
            logging.debug("Found link with href %s" % href)
            if href_safe in self.tg.entities:
                e = self.tg.entities[href_safe]
                if e.url:
                    link.set('href', e.url)
                    if not link.text:
                        link.text = str(e.skos_prefLabel)
                    if F.Content not in e.rdf_type: # FIXME: duplicates logic from build
                        link.set('rel', str(F.mentions))
                else:
                    link.set('value', href)
                    link.attrib.pop('href')
                    link.tag = 'data'
            else:
                pass # sometimes a link is just a link

class ImgRewriteExtension(markdown.extensions.Extension):
    def __init__(self, **kwargs):
        self.config = {'tg' : ['This has to be a string for reasons', 'The templatablegraph to query for embedded items'],
                       'base' : ['http://example.org/', 'The base URI to use when embedded content is specified as a relative URL']}
        super(ImgRewriteExtension, self).__init__(**kwargs)

    def extendMarkdown(self, md, md_globals):
        img_rw = ImgRewriter(md, self.getConfig('tg'), self.getConfig('base'))
        md.treeprocessors.add('imgrewrite', img_rw, '>inline')

def save_ipfs(ipfs_client, r, ipfs_dir):
    if not r.id.startswith("/ipfs/"):
        raise IOError("%s: not a NURI, can't save" % r.id)
    logging.debug("%s: saving to %s" % (r.id, ipfs_dir))
    cwd = os.getcwd()
    subdir = os.path.dirname(r.id[6:]) # use local OS path as we just want to create dirs necessary for the path to work on this system
    if subdir:
        ipfs_dir = os.path.join(ipfs_dir, subdir)
    os.makedirs(ipfs_dir, exist_ok=True)
    os.chdir(ipfs_dir)
    ipfs_client.get(r.id)
    os.chdir(cwd)

def get_page_path(e, ctx, e_type, output_dir, file_type='html'):
    return os.path.join(output_dir, e_type.safe, ctx, e+'.'+file_type)

def get_page_url(e, ctx, e_type, url_base, file_type='html'):
    return '%s/%s/%s/%s' % (url_base, e_type.safe, ctx, e+'.'+file_type)

def resolve_embed(m, tg, e, in_p=False):
    logging.debug("Resolving embed %s" % (m.group(1)))
    attrs = {}
    for mm in re.finditer('(\S+)="([^"]*)"', m.group(1)):
        attrs[mm.group(1)]=mm.group(2)

    with open(attrs['src'],'r') as f:
        return f.read()

def get_html_body_for_rendition(tg, e, r, ipfs_client, markdown_processor, ipfs_dir):
    def get_charset(r, e, mt):
        for c in r.charset:
            return c
        else:
            logging.warning("%s: no charset for %s rendition %s" % (e, mt, repr(r)))
            return 'utf-8'

    mt = r.mediaType

    logging.debug('%s: using %s rendition' % (e, mt))

    if rdflib.Literal('text/markdown') in mt:
        # markdown is a partial page so safe to embed
        return markdown_processor.convert(ipfs_client.cat(r.id).decode(get_charset(r, e, mt)))

    for m in mt:
        if m.startswith('image/'):
            return '<img src="%s" alt="%s">' % (r.blobURL, e.caption)

    if rdflib.Literal('text/html') in mt:
        # html is assumed to be a complete page
        return '<div class="__embed__"><iframe src="%s"></iframe></div>' % r.blobURL

    if rdflib.Literal('application/pdf') in mt:
        return '<div class="__embed__"><embed src="%s" type="application/pdf"></embed></div>' % r.blobURL

    logging.info("%s: media type %s is not a suitable body" % (e, mt))
    return None

def find_renditions_for_context(rr, ctx):
    for r in rr:
        for u in r.intendedUse:
            logging.debug("intended use %s %s" % (u,ctx))
            if u == ctx:
                out = []
                for s in rr:
                    for v in s.intendedUse:
                        if u == v:
                            out.append(s)
                return out
    for f in ctx.fallback:
        out = find_renditions_for_context(rr, f)
        if out:
            return out
    return []


def get_html_body(tg, e, ctx, ipfs_client, markdown_processor, ipfs_dir):
    rr = e.rendition
    available = find_renditions_for_context(rr, ctx)
    logging.debug("%s: %s of %s renditions available for context %s" % (e.id, len(available), len(rr), ctx))

    for r in available:
        eh = get_html_body_for_rendition(tg, e, r, ipfs_client, markdown_processor, ipfs_dir)
        if eh:
            return eh

    return '<!-- non-renderable item %s (tried %s) -->' % (e, available)

def publish_graph(g, cfg):
    tg = TemplatableGraph(g)

    jinja_e = jinja2.Environment(
        loader=jinja2.FileSystemLoader(cfg.template_dir),
        autoescape=True
    )

    markdown_processor = markdown.Markdown(output_format="html5", extensions=[ImgRewriteExtension(tg=tg, base=cfg.id_base)])

    embed_html = {}
    stage = {}
    to_write = set()
    home_page = None

    for e_safe, e in tg.entities.items():
        stage[e] = {}

        for ctx in HTML_FOR_CONTEXT:
            ctx_safe = tg.safePath(ctx)
            # use the most direct type because we need to go up in a specific order
            # FIXME: provide ordered walk functions on entities?
            e_types = e.type()

            while e_types:
                for e_type in e_types:
                    t_path = os.path.join(ctx_safe, e_type.safe)
                    try:
                        t = jinja_e.get_template(t_path)
                    except jinja2.exceptions.TemplateNotFound as err:
                        # bit verbose logging.debug("%s: no template at %s" % (e, t_path))
                        continue

                    dest = get_page_path(e_safe, ctx_safe, e_type, cfg.output_dir)
                    url = get_page_url(e_safe, ctx_safe, e_type, cfg.url_base)

                    logging.debug('%s: will render for %s as %s -> %s' % (e, ctx, e_type, dest))
                    stage[e][ctx] = (t, dest)
                    if e.id == rdflib.URIRef(cfg.home_site) and ctx == F.page:
                        home_page = url

                    e_types = None # found a renderable type

                    if ctx == F.page:
                        if e.url:
                            # graph specified the URL, don't make one
                            break
                        elif F.WebPage in e.rdf_type:
                            # object is a Web page whose ID is its URL
                            tg.add(e.id, F.url, e.id)
                            break
                        else:
                            # add the computed URL of the item as a full page, for templates to pick up
                            tg.add(e.id, F.url, rdflib.Literal(url))
                    elif ctx == F.embed:
                        # add the embed path, for the post-template stitcher to pick up
                        tg.add(e.id, F.embedPath, rdflib.Literal(dest))

                    to_write.add(dest)

                    break

                if e_types is not None:
                    # get the next layer of types
                    e_types = e_types.rdfs_subClassOf

    logging.info("Stage is ready")

    progress = True
    while progress:
        progress = False
        logging.info("%s pages left to render" % len(to_write))
        for e, dests in stage.items():
            for r in e.rendition:
                # put all the renditions up, in case referenced by a template
                save_ipfs(cfg.ipfs_client, r, cfg.ipfs_dir)


            for embed in e.includes:
                if F.embed in stage[embed]:
                    et, ed = stage[embed][F.embed]
                else:
                    raise PublishError("%s: need %s but there is no way to embed it" % (e, embed))
                if ed in to_write:
                    logging.debug("%s: can't write %s yet, need %s" % (e, dest, ed))
                    break
                logging.debug("%s: %s looks good" % (e, ed))
            else:
                # get the rendition html for all contexts even if there won't be a page
                for ctx in HTML_FOR_CONTEXT:
                    ctx_safe = tg.safePath(ctx)
                    body = get_html_body(tg, e, tg.entities[ctx_safe], cfg.ipfs_client, markdown_processor, cfg.ipfs_dir)

                    body = re.sub("<p>\s*<false-embed([^>]*)>\s*</false-embed>\s*</p>", lambda m: resolve_embed(m, tg, e, True), body)
                    body = re.sub("<p>\s*<false-embed([^>]*)>\s*</p>", lambda m: resolve_embed(m, tg, e, True), body)
                    body = re.sub("<false-embed([^>]*)>\s*</false-embed>", lambda m: resolve_embed(m, tg, e, True), body)
                    body = re.sub("<false-embed([^>]*)>", lambda m: resolve_embed(m, tg, e, False), body)

                    tg.add(e.id, HTML_FOR_CONTEXT[ctx], rdflib.Literal(body))

                # now build the pages
                for ctx in dests:
                    tpl, dest = dests[ctx]
                    content = tpl.render(e.po)

                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    logging.debug("%s: writing %s" % (e, dest))
                    with open(dest,'w') as f:
                        f.write(content)

                    if dest in to_write:
                        to_write.remove(dest)
                        progress = True

    if to_write:
        raise PublishError("Embed loop, can't render these: %s" % '\n'.join(to_write))
    else:
        logging.info("All written successfully.")

    return home_page
