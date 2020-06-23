#!/usr/bin/python3

import rdflib
import sys, logging, os, re, urllib.parse, shutil, datetime, subprocess
import jinja2, markdown
import pprint
from false.graph import *

EXTERNAL_LINKS = {
  "http://www.wikidata.org/wiki/\\1": re.compile("http://www.wikidata.org/entity/(.*)")
}

F = rdflib.Namespace("http://id.colourcountry.net/false/")
TEMP_IPFS = rdflib.Namespace("ipfs:/") # our temporary URI scheme
TRUE_IPFS = rdflib.Namespace("/ipfs/")

# TODO: put this per-context configuration into the graph
HTML_FOR_CONTEXT = { F.link: F.linkHTML, F.teaser: F.teaserHTML, F.embed: F.embedHTML, F.page: F.pageHTML }

PUB_FAIL_MSG = """
---------------------------------------------------------------------------------
PUBLISH FAILED
These entities couldn't be rendered:
"""

def ipfs_cat(path,cache_dir=None):
    n = re.match("/ipfs/(.*)$",path)
    if not n:
        raise PublishError(f"{path} is not a NURI, can't cat it")

    if cache_dir:
        try:
            r = open(os.path.join(cache_dir,n.group(1)),"rb").read()
            logging.info(f"Found cached blob for {path}")
            return r
        except IOError as e:
            logging.info(f"Cache miss for {path}: {e}")

    r = subprocess.run(["ipfs","cat",n.group(1)], stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
    logging.info(f"IPFS cat {path}: {r}")
    return r

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
        md.treeprocessors.add('imgrewrite', img_rw, '>inline')

def get_page_path(e_safe, ctx_safe, e_type, output_dir, file_type='html'):
    return os.path.join(output_dir, e_type.safe, ctx_safe, e_safe+'.'+file_type)

def get_page_url(e_safe, ctx_safe, e_type, url_base, file_type='html'):
    return '{base}/{type}/{ctx}/{e}.{ft}'.format(base=url_base, type=e_type.safe, ctx=ctx_safe, e=e_safe, ft=file_type)

def resolve_content_reference(m, tg, base, stage, e, upgrade_to_teaser=False):
    logging.debug("Resolving content reference {ref}".format(ref=m.group(1)))

    attrs = {}
    for mm in re.finditer('(\S+)="([^"]*)"', m.group(1)):
        attrs[mm.group(1)]=mm.group(2)

    if attrs["src"].startswith("_:"): # don't resolve blank nodes
        src = attrs["src"]
    else:
        src = urllib.parse.urljoin(base, attrs["src"])
    src_safe = tg.safePath(src)

    if "context" in attrs:
        ctx = rdflib.URIRef(attrs["context"])
        if upgrade_to_teaser and ctx==F.link:
            ctx = F.teaser
    else:
        ctx = F.teaser

    if src_safe not in tg.entities:
        r = "can't resolve {src}@@{ctx}: not in universe".format(src=src,ctx=ctx)
        logging.warning(r)
        logging.warning("Entities available: {e}".format(e="\n".join(sorted(tg.entities.keys()))))
        return "<!-- {r} -->".format(r=r)
    elif (tg.entities[src_safe], ctx) not in stage:
        if tg.entities[src_safe].isBlankNode():
            # an unstaged blank node is not an error
            return ""
        r = "can't resolve {src}@@{ctx}: not staged".format(src=src,ctx=ctx)
        logging.warning(r)
        return "<!-- {r} -->".format(r=r)

    # the stage has (template, path) for each context so [1] references the path
    fn = stage[(tg.entities[src_safe],ctx)][1]
    try:
        with open(fn,'r') as f:
            return f.read()
    except FileNotFoundError:
        raise PublishNotReadyError("requires {src}@@{ctx}".format(src=src,ctx=ctx))

def get_html_body_for_rendition(tg, e, r, markdown_processor, cache_dir):
    def get_charset(r, e, mt):
        for c in r.charset:
            return c
        else:
            logging.warning(f"{e}: no charset for {mt} rendition {repr(r)}")
            return 'utf-8'

    mt = r.mediaType

    if rdflib.Literal('text/markdown') in mt:
        logging.info(f"{e.id}: using markdown rendition {r.id}")
        content = ipfs_cat(r.id, cache_dir)

        return markdown_processor.convert(content.decode(get_charset(r, e, mt)))

    blobURL = r.blobURL.pick().id

    # FIXME: put this stuff into configuration and templates, not here

    for m in mt:
        if m.startswith('image/'):
            logging.debug(f"{e.id}: using {m} rendition")
            return '<img src="{url}">'.format(url=blobURL)

    if rdflib.Literal('text/html') in mt:
        # html is assumed to be a complete page
        logging.debug('{e}: using html rendition'.format(e=e.id))
        return '<div class="__embed__"><iframe src="{url}"></iframe></div>'.format(url=blobURL)

    if rdflib.Literal('application/pdf') in mt:
        logging.debug('{e}: using pdf rendition'.format(e=e.id))
        return '<div class="__embed__"><embed src="{url}" type="application/pdf"></embed></div>'.format(url=blobURL)

    for m in mt:
        # just pick one and hope there's a script in the template that can render it
        logging.debug('{e}: using {m} rendition'.format(e=e.id, m=m))
        return '<div class="__embed__"><embed src="{url}" type="{m}"></embed></div>'.format(url=blobURL, m=m)

    return None # there weren't any media types

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


def get_html_body(tg, e, ctx, markdown_processor, cache_dir):
    rr = e.get('rendition')
    available = find_renditions_for_context(rr, ctx)
    logging.debug("{e}@@{ctx}: {n} of {m} renditions are suitable".format(e=e.id, ctx=ctx.id, n=len(available), m=len(rr)))

    for r in available:
        eh = get_html_body_for_rendition(tg, e, r, markdown_processor, cache_dir)
        if eh:
            return eh

    return '<!-- {e}@@{ctx} (tried {av}) -->'.format(e=e.id, ctx=ctx.id, av=available)

def publish_graph(g, cfg):

    # Fix up everywhere there is an IPFS uri

    count = 0
    for s,p,o in g.triples((None, None, None)):
        new_s = None
        new_o = None
        if s.startswith(TEMP_IPFS):
            new_s = TRUE_IPFS[s[len(TEMP_IPFS):]]
        if o.startswith(TEMP_IPFS):
            new_o = TRUE_IPFS[o[len(TEMP_IPFS):]]
        if new_s or new_o:
            count += 1
            g.remove((s,p,o))
            g.add((new_s or s, p, new_o or o))
    logging.info(f"Fixed up {count} IPFS URLs")

    tg = TemplatableGraph(g)

    def get_time_now():
        return datetime.datetime.utcnow().isoformat()

    jinja_e = jinja2.Environment(
        loader=jinja2.FileSystemLoader(cfg.template_dir),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    jinja_e.globals["now"] = get_time_now

    markdown_processor = markdown.Markdown(output_format="html5", extensions=[ImgRewriteExtension(tg=tg, base=cfg.id_base), 'tables'])

    embed_html = {}
    entities_to_write = set()
    stage = {}
    home_page = None
    cache_dir = os.path.join(cfg.output_dir, "ipfs")
    for e_safe, e in tg.entities.items():
        allTypes = e.get('rdf_type')
        if not allTypes:
            logging.debug("{e}: unknown type? {debug}".format(e=e.id, debug=e.debug()))
            continue

        if F.WebPage in allTypes:
            if hasattr(e, 'url'):
                if not e.isBlankNode():
                    raise PublishError("{e}: WebPage ({tt}), must not have :url (got {url}). Use the ID as the URL.".format(e=e.id, url=e.url, tt=allTypes))
            else:
                if e.isBlankNode():
                    raise PublishError("{e}: WebPage which is a blank node must have a :url property.".format(e=e.id))
                tg.add(e.id, F.url, rdflib.Literal(e.id))

        for ctx_id in HTML_FOR_CONTEXT:
            ctx_safe = tg.safePath(ctx_id)

            if e.isBlankNode() and ctx_id != F.embed:
                # blank nodes don't have a canonical location,
                # so can't be linked to and can't have a page,
                # but they can be embedded
                logging.debug("{e}@@{ctx}: not staging blank node".format(e=e.id, ctx=ctx_id))
                continue

            # use the most direct type because we need to go up in a specific order
            # TODO: provide ordered walk functions on entities?
            e_types = e.type()

            dest = None

            while e_types:
                for e_type in e_types:
                    t_path = os.path.join(ctx_safe, e_type.safe)
                    try:
                        tpl = jinja_e.get_template(t_path)
                    except jinja2.exceptions.TemplateNotFound as err:
                        logging.debug("{e}: no template at {path}".format(e=e.id, path=t_path))
                        continue

                    dest = get_page_path(e_safe, ctx_safe, e_type, cfg.output_dir)
                    url = get_page_url(e_safe, ctx_safe, e_type, cfg.url_base)
                    e_types = None # found a renderable type
                    break

                if e_types is not None:
                    # get the next layer of types
                    e_types = e_types.get('rdfs_subClassOf')
                    logging.debug("{e}@@{ctx}: no template for direct type, trying {types}".format(e=e.id, ctx=ctx_id, types=repr(e_types)))

            if dest is None:
                logging.debug("{e}@@{ctx}: no template available".format(e=e.id, ctx=ctx_id))
                continue

            logging.debug('{e}@@{ctx}: will render as {type} -> {dest}'.format(e=e.id, ctx=ctx_id, type=e_type.id, dest=dest))
            stage[(e, ctx_id)]=(tpl, dest)
            entities_to_write.add(e)

            if ctx_id == F.page and 'url' not in e:
                # add the computed URL of the item as a full page, for templates to pick up
                tg.add(e.id, F.url, rdflib.Literal(url))

            if e.id == rdflib.URIRef(cfg.home_site):
                home_page = url


    logging.info("Stage is ready: {n} destinations, {m} entities".format(n=len(stage), m=len(entities_to_write)))

    if not home_page:
        raise PublishError("Home page {home} is not staged, can't continue".format(home=cfg.home_site))

    iteration = 0
    to_write = set(stage.keys())
    progress = True
    while progress:
        iteration += 1
        progress = False
        logging.info("Publish iteration {i}: {n} of {m} destinations left".format(i=iteration, n=len(to_write), m=len(stage)))
        next_write = set()

        # now build the HTML for everything in the different contexts and add to the graph
        for item in to_write:
            item = item[:2]
            e, ctx_id = item
            tpl, dest = stage[item]
            htmlProperty = HTML_FOR_CONTEXT[ctx_id]

            if htmlProperty in e:
                raise PublishError("{e}: already have inner html for {ctx}".format(e=e.id, ctx=ctx_id))
                continue

            ctx_safe = tg.safePath(ctx_id)
            body = get_html_body(tg, e, tg.entities[ctx_safe], markdown_processor, cache_dir)

            # Add the inner (markdown-derived) html to the graph for templates to pick up
            logging.debug("Adding this inner html as {prop} to {e}@@{ctx}:\n{html}...".format(prop=htmlProperty, e=e.id, ctx=ctx_id, html=body[:100]))
            tg.add(e.id, htmlProperty, rdflib.Literal(body))

            try:
                content = e.render(tpl)
            except (jinja2.exceptions.UndefinedError, RequiredAttributeError) as err:
                # If an attribute is missing it may be a body for another entity/context that is not yet rendered
                logging.debug("{e}@@{ctx} not ready for {tpl}: {err}".format(e=e.id, ctx=ctx_id, tpl=tpl, err=err))
                next_write.add((e, ctx_id, err))
                continue

            try:
                content = re.sub("<p>\s*<em>\s*<false-content([^>]*)>\s*</false-content>\s*</em>\s*</p>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), content)
                content = re.sub("<p>\s*<em>\s*<false-content([^>]*)>\s*</em>\s*</p>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), content)
                content = re.sub("<p>\s*<false-content([^>]*)>\s*</false-content>\s*</p>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, False), content)
                content = re.sub("<p>\s*<false-content([^>]*)>\s*</p>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, False), content)
                content = re.sub("<em>\s*<false-content([^>]*)>\s*</false-content>\s*</em>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), content)
                content = re.sub("<em>\s*<false-content([^>]*)>\s*</em>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True), content)
                content = re.sub("<false-content([^>]*)>\s*</false-content>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, False), content)
                content = re.sub("<false-content([^>]*)>", lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, False), content)
            except PublishNotReadyError as err:
                logging.debug("{e}@@{ctx} deferred: {err}".format(e=e.id, ctx=ctx_id, err=err))
                next_write.add((e, ctx_id, err))
                continue

            os.makedirs(os.path.dirname(dest), exist_ok=True)
            logging.debug("{e}@@{ctx}: writing {dest}".format(e=e.id, ctx=ctx_id, dest=dest))
            with open(dest,'w') as f:
                f.write(content)

            progress = True

        to_write = next_write

    if to_write:
        err_list = []
        for item in to_write:
            err_list.append("{e}@@{ctx}: {err}".format(e=item[0].id, ctx=item[1], err=item[2]))
        raise PublishError("{msg}\n     {detail}\n\n".format(msg=PUB_FAIL_MSG, detail='\n     '.join(err_list)))
    else:
        logging.info("All written successfully.")

    return home_page
