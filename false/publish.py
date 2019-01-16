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

# TODO: put this per-context configuration into the graph
HTML_FOR_CONTEXT = { F.link: F.linkHTML, F.teaser: F.teaserHTML, F.embed: F.embedHTML, F.page: F.pageHTML }

# Teasers can link to anything, anywhere, so external stuff comes out as a teaser
FALLBACK_CONTEXTS_FOR_EXTERNAL_RESOURCES = { F.embed: F.teaser, F.page: F.teaser }

# Only content can be embedded, so embedded non-content comes out as a teaser
FALLBACK_CONTEXTS_FOR_NON_CONTENT = { F.embed: F.teaser }

PUB_FAIL_MSG = """
---------------------------------------------------------------------------------
PUBLISH FAILED
These entities couldn't be rendered:
"""

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

        for parent in doc.findall('.//img/..'):
            for image in parent.findall('.//img'):
                src = urllib.parse.urljoin(self.base, image.get('src'))
                logging.debug("Found image with src %s" % src)
                src_safe = self.tg.safePath(src)
                if src_safe in self.tg.entities:
                    image.set('src', src)
                    image.set('context', F.embed)
                    image.tag = 'false-content'
                elif src.startswith(self.base):
                    # this is our ID but we do not have it because it is private, or because it didn't get defined
                    logging.info("removing embed of unknown or private entity %s" % src)
                    parent.remove(image)
                else:
                    # probably an image on the internet
                    pass

        for parent in doc.findall('..//a/..'):
            for link in doc.findall('.//a'):
                href = urllib.parse.urljoin(self.base, link.get('href'))
                href_safe = self.tg.safePath(href)
                logging.debug("Found link with href %s" % href)
                if href_safe in self.tg.entities:
                    e = self.tg.entities[href_safe]
                    if 'url' in e:
                        link.set('href', e.url)
                        if not link.text:
                            link.text = str(e.skos_prefLabel)
                        if F.Content not in e.rdf_type: # FIXME: duplicates logic from build
                            link.set('rel', str(F.mentions))
                    else:
                        link.set('value', href)
                        link.attrib.pop('href')
                        link.tag = 'data'
                elif href.startswith(self.base):
                    # this is our ID but we do not have it because it is private, or because it didn't get defined
                    logging.info("removing link to unknown entity %s" % href)
                    parent.remove(link)
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

def get_page_path(e_safe, ctx_safe, e_type, output_dir, file_type='html'):
    return os.path.join(output_dir, e_type.safe, ctx_safe, e_safe+'.'+file_type)

def get_page_url(e_safe, ctx_safe, e_type, url_base, file_type='html'):
    return '%s/%s/%s/%s' % (url_base, e_type.safe, ctx_safe, e_safe+'.'+file_type)

def resolve_content_reference(m, tg, base, stage, e, in_p=False):
    logging.debug("Resolving content reference %s" % (m.group(1)))

    attrs = {}
    for mm in re.finditer('(\S+)="([^"]*)"', m.group(1)):
        attrs[mm.group(1)]=mm.group(2)

    if attrs["src"].startswith("_:"): # don't resolve blank nodes
        src = attrs["src"]
    else:
        src = urllib.parse.urljoin(base, attrs["src"])
    src_safe = tg.safePath(src)
    ctx = rdflib.URIRef(attrs["context"])

    if (tg.entities[src_safe], ctx) not in stage:
        raise PublishError("can't resolve %s@@%s: not staged" % (src, ctx))

    # the stage has (template, path) for each context so [1] references the path
    fn = stage[(tg.entities[src_safe],ctx)][1]
    try:
        with open(fn,'r') as f:
            return f.read()
    except FileNotFoundError:
        raise PublishNotReadyError("requires %s@@%s" % (src, ctx))

def get_html_body_for_rendition(tg, e, r, ipfs_client, markdown_processor, ipfs_dir):
    def get_charset(r, e, mt):
        for c in r.charset:
            return c
        else:
            logging.warning("%s: no charset for %s rendition %s" % (e, mt, repr(r)))
            return 'utf-8'

    mt = r.mediaType

    logging.debug('%s: using %s rendition' % (e.id, mt))

    if rdflib.Literal('text/markdown') in mt:
        # markdown is a partial page so safe to embed
        return markdown_processor.convert(ipfs_client.cat(r.id).decode(get_charset(r, e, mt)))

    blobURL = r.blobURL.pick().id

    for m in mt:
        if m.startswith('image/'):
            return '<img src="%s">' % blobURL

    if rdflib.Literal('text/html') in mt:
        # html is assumed to be a complete page
        return '<div class="__embed__"><iframe src="%s"></iframe></div>' % blobURL

    if rdflib.Literal('application/pdf') in mt:
        return '<div class="__embed__"><embed src="%s" type="application/pdf"></embed></div>' % blobURL

    logging.info("%s: media type %s is not a suitable body" % (e.id, mt))
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
    for f in ctx.get('fallback'):
        out = find_renditions_for_context(rr, f)
        if out:
            return out
    return []


def get_html_body(tg, e, ctx, ipfs_client, markdown_processor, ipfs_dir):
    rr = e.get('rendition')
    available = find_renditions_for_context(rr, ctx)
    logging.debug("%s@@%s: %s of %s renditions available" % (e.id, ctx.id, len(available), len(rr)))

    for r in available:
        eh = get_html_body_for_rendition(tg, e, r, ipfs_client, markdown_processor, ipfs_dir)
        if eh:
            return eh

    return '<!-- %s@@%s (tried %s) -->' % (e.id, ctx.id, available)

def publish_graph(g, cfg):
    tg = TemplatableGraph(g)

    jinja_e = jinja2.Environment(
        loader=jinja2.FileSystemLoader(cfg.template_dir),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True
    )

    markdown_processor = markdown.Markdown(output_format="html5", extensions=[ImgRewriteExtension(tg=tg, base=cfg.id_base)])

    embed_html = {}
    entities_to_write = set()
    stage = {}
    home_page = None

    for e_safe, e in tg.entities.items():
        allTypes = e.get('rdf_type')
        if not allTypes:
            logging.debug("%s: unknown type" % e.id)
            continue

        if F.WebPage in allTypes:
            if hasattr(e, 'url'):
                if not e.isBlankNode():
                    raise PublishError("Entities defining web pages must not have a :url property. Use the ID as the URL.")
            else:
                if e.isBlankNode():
                    raise PublishError("Blank nodes defining web pages must have a :url property.")
                tg.add(e.id, F.url, e.id)

        for ctx_id in HTML_FOR_CONTEXT:
            eff_ctx_safe = ctx_safe = tg.safePath(ctx_id)

            if F.Content not in allTypes and ctx_id in FALLBACK_CONTEXTS_FOR_NON_CONTENT:
                logging.debug("%s@@%s: rendering non-content as if for %s" % (e.id, ctx_id, FALLBACK_CONTEXTS_FOR_NON_CONTENT[ctx_id]))
                eff_ctx_safe = tg.safePath(FALLBACK_CONTEXTS_FOR_NON_CONTENT[ctx_id])

            if 'url' in e and ctx_id in FALLBACK_CONTEXTS_FOR_EXTERNAL_RESOURCES:
                logging.debug("%s@@%s: rendering external entity as if for %s" % (e.id, ctx_id, FALLBACK_CONTEXTS_FOR_EXTERNAL_RESOURCES[ctx_id]))
                eff_ctx_safe = tg.safePath(FALLBACK_CONTEXTS_FOR_EXTERNAL_RESOURCES[ctx_id])

            if e.isBlankNode() and ctx_id != F.embed:
                # blank nodes don't have a canonical location,
                # so can't be linked to and can't have a page,
                # but they can be embedded
                logging.debug("%s@@%s: not staging blank node" % (e.id, ctx_id))
                continue

            # use the most direct type because we need to go up in a specific order
            # TODO: provide ordered walk functions on entities?
            e_types = e.type()

            dest = None

            while e_types:
                for e_type in e_types:
                    t_path = os.path.join(eff_ctx_safe, e_type.safe)
                    try:
                        tpl = jinja_e.get_template(t_path)
                    except jinja2.exceptions.TemplateNotFound as err:
                        logging.debug("%s: no template at %s" % (e.id, t_path))
                        continue

                    dest = get_page_path(e_safe, ctx_safe, e_type, cfg.output_dir)
                    url = get_page_url(e_safe, ctx_safe, e_type, cfg.url_base)
                    e_types = None # found a renderable type
                    break

                if e_types is not None:
                    # get the next layer of types
                    e_types = e_types.get('rdfs_subClassOf')
                    logging.debug("%s@@%s: no template for direct type, trying %s" % (e.id, ctx_id, repr(e_types)))

            if dest is None:
                logging.debug("%s@@%s: no template available" % (e.id, ctx_id))
                continue

            logging.debug('%s@@%s: will render as %s -> %s' % (e.id, ctx_id, e_type.id, dest))
            stage[(e, ctx_id)]=(tpl, dest)
            entities_to_write.add(e)

            if ctx_id == F.page and 'url' not in e:
                # add the computed URL of the item as a full page, for templates to pick up
                tg.add(e.id, F.url, rdflib.Literal(url))

            if e.id == rdflib.URIRef(cfg.home_site):
                home_page = url


    logging.info("Stage is ready: %s destinations, %s entities" % (len(stage), len(entities_to_write)))

    if not home_page:
        raise PublishError("Home page %s is not staged, can't continue" % cfg.home_site)

    # first put all the renditions up, in case referenced by a template
    for e in entities_to_write:
        for r in e.get('rendition'):
            save_ipfs(cfg.ipfs_client, r, cfg.ipfs_dir)

    iteration = 0
    to_write = set(stage.keys())
    progress = True
    while progress:
        iteration += 1
        progress = False
        logging.info("Publish iteration %d: %s of %s destinations left" % (iteration, len(to_write), len(stage)))
        next_write = set()

        # now build the HTML for everything in the different contexts and add to the graph
        for item in to_write:
            item = item[:2]
            e, ctx_id = item
            tpl, dest = stage[item]
            htmlProperty = HTML_FOR_CONTEXT[ctx_id]

            if htmlProperty in e:
                raise PublishError("%s: already have inner html for %s" % (e.id, ctx_id))
                continue

            ctx_safe = tg.safePath(ctx_id)
            body = get_html_body(tg, e, tg.entities[ctx_safe], cfg.ipfs_client, markdown_processor, cfg.ipfs_dir)

            # Add the inner (markdown-derived) html to the graph for templates to pick up
            logging.debug("Adding this inner html to %s@@%s:\n%s..." % (e.id, ctx_id, body[:100]))
            tg.add(e.id, htmlProperty, rdflib.Literal(body))

            try:
                content = e.render(tpl)
            except (jinja2.exceptions.UndefinedError, RequiredAttributeError) as err:
                # If an attribute is missing it may be a body for another entity/context that is not yet rendered
                logging.debug("%s@@%s not ready for %s: %s" % (e.id, ctx_id, tpl, err))
                next_write.add((e, ctx_id, err))
                continue

            try:
                content = re.sub("<p>\s*<false-content([^>]*)>\s*</false-content>\s*</p>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), content)
                content = re.sub("<p>\s*<false-content([^>]*)>\s*</p>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), content)
                content = re.sub("<false-content([^>]*)>\s*</false-content>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), content)
                content = re.sub("<false-content([^>]*)>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, False), content)
            except PublishNotReadyError as err:
                logging.debug("%s@@%s deferred: %s" % (e.id, ctx_id, err))
                next_write.add((e, ctx_id, err))
                continue

            os.makedirs(os.path.dirname(dest), exist_ok=True)
            logging.debug("%s@@%s: writing %s" % (e.id, ctx_id, dest))
            with open(dest,'w') as f:
                f.write(content)

            progress = True

        to_write = next_write

    if to_write:
        err_list = []
        for item in to_write:
            err_list.append("%s@@%s: %s" % (item[0].id, item[1], item[2]))
        raise PublishError("%s\n     %s\n\n" % (PUB_FAIL_MSG, '\n     '.join(err_list)))
    else:
        logging.info("All written successfully.")

    return home_page
