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

class PublishNotReadyError(PublishError):
    pass

class ImgRewriter(markdown.treeprocessors.Treeprocessor):
    def __init__(self, md, tg, base):
        self.tg = tg
        self.base = base
        super(ImgRewriter, self).__init__(md)

    def run(self, doc):
        for embed in doc.findall('.//false-embed'):
            embed.tag = 'false-content'
            embed.set('context', F.embed)
            embed.set('src', urllib.parse.urljoin(self.base, embed.get('src')))

        for teaser in doc.findall('.//false-teaser'):
            teaser.tag = 'false-content'
            teaser.set('context', F.teaser)
            teaser.set('src', urllib.parse.urljoin(self.base, teaser.get('src')))

        for image in doc.findall('.//img'):
            src = urllib.parse.urljoin(self.base, image.get('src'))
            logging.debug("Found image with src %s" % src)
            src_safe = self.tg.safePath(src)
            if src_safe in self.tg.entities:
                image.set('src', src)
                image.set('context', F.embed)
                image.tag = 'false-content'
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

def resolve_content_reference(m, tg, base, stage, e, in_p=False):
    logging.debug("Resolving content reference %s" % (m.group(1)))

    attrs = {}
    for mm in re.finditer('(\S+)="([^"]*)"', m.group(1)):
        attrs[mm.group(1)]=mm.group(2)

    src = urllib.parse.urljoin(base, attrs["src"])
    src_safe = tg.safePath(src)
    ctx = rdflib.URIRef(attrs["context"])

    if ctx not in stage[tg.entities[src_safe]]:
        logging.warning("can't publish %s: version for context %s is not staged" % (src, ctx))
        raise PublishNotReadyError

    # the stage has (template, path) for each context so [1] references the path
    try:
        with open(stage[tg.entities[src_safe]][ctx][1],'r') as f:
            return f.read()
    except FileNotFoundError:
        logging.debug("can't publish %s: version for context %s has not yet been built" % (src, ctx))
        raise PublishNotReadyError

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
        dests_by_context = {}

        for ctx in HTML_FOR_CONTEXT:
            ctx_safe = tg.safePath(ctx)
            # use the most direct type because we need to go up in a specific order
            # FIXME: provide ordered walk functions on entities?
            e_types = e.type()

            while e_types:
                for e_type in e_types:
                    t_path = os.path.join(ctx_safe, e_type.safe)
                    try:
                        tpl = jinja_e.get_template(t_path)
                    except jinja2.exceptions.TemplateNotFound as err:
                        # bit verbose logging.debug("%s: no template at %s" % (e, t_path))
                        continue

                    dest = get_page_path(e_safe, ctx_safe, e_type, cfg.output_dir)
                    url = get_page_url(e_safe, ctx_safe, e_type, cfg.url_base)

                    if e.id == rdflib.URIRef(cfg.home_site) and ctx == F.page:
                        home_page = url

                    e_types = None # found a renderable type

                    # FIXME: some contexts are still valid here (like teaser)
                    if e.url:
                        # graph specified the URL, don't make one
                        break
                    elif F.WebPage in e.rdf_type:
                        # object is a Web page whose ID is its URL
                        tg.add(e.id, F.url, e.id)
                        break

                    if ctx == F.page:
                        # add the computed URL of the item as a full page, for templates to pick up
                        tg.add(e.id, F.url, rdflib.Literal(url))

                    logging.debug('%s: will render for %s as %s -> %s' % (e, ctx, e_type, dest))
                    dests_by_context[ctx] = (tpl, dest)
                    to_write.add(dest)
                    break

                if e_types is not None:
                    # get the next layer of types
                    e_types = e_types.rdfs_subClassOf

        if dests_by_context:
            stage[e] = dests_by_context

    logging.info("Stage is ready: %s entities" % len(stage))

    for e in stage:
        s = []
        for ctx in stage[e]:
            if stage[e][ctx][1] in to_write:
                s.append(str(ctx))
            else:
                s.append(None)
        logging.debug("%s: %s" % (e, s))

    iteration = 0
    written = set()
    progress = True
    while progress:
        iteration += 1
        progress = False
        logging.info("Publish iteration %d: %s pages rendered, %s pages left" % (iteration, len(written), len(to_write)))
        logging.debug(to_write)

        # first put all the renditions up, in case referenced by a template
        for e, dests in stage.items():
            for r in e.rendition:
                save_ipfs(cfg.ipfs_client, r, cfg.ipfs_dir)

        # now build the HTML for everything in the different contexts and add to the graph
        for e, dests in stage.items():
            for ctx in HTML_FOR_CONTEXT:
                ctx_safe = tg.safePath(ctx)
                try:
                    body = get_html_body(tg, e, tg.entities[ctx_safe], cfg.ipfs_client, markdown_processor, cfg.ipfs_dir)

                    body = re.sub("<p>\s*<false-content([^>]*)>\s*</false-content>\s*</p>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), body)
                    body = re.sub("<p>\s*<false-content([^>]*)>\s*</p>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), body)
                    body = re.sub("<false-content([^>]*)>\s*</false-content>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), body)
                    body = re.sub("<false-content([^>]*)>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, False), body)

                    tg.add(e.id, HTML_FOR_CONTEXT[ctx], rdflib.Literal(body))
                except PublishNotReadyError:
                    continue # maybe it'll be ready next time round

        # finally build and save out all the pages
        for e, dests in stage.items():
            for ctx in dests:
                tpl, dest = dests[ctx]
                if dest in written:
                    logging.debug("%s: already wrote %s" % (e, dest))
                    continue

                content = tpl.render(e.po)

                os.makedirs(os.path.dirname(dest), exist_ok=True)
                logging.debug("%s: writing %s" % (e, dest))
                with open(dest,'w') as f:
                    f.write(content)

                to_write.remove(dest)
                written.add(dest)
                progress = True

        if not to_write:
            break

    if to_write:
        raise PublishError("Embed loop, can't render these: %s" % '\n'.join(to_write))
    else:
        logging.info("All written successfully.")

    return home_page
