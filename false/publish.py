#!/usr/bin/python3

import rdflib
import sys, logging, os, re, urllib.parse, shutil, datetime, subprocess
import jinja2, pprint, traceback

from false.graph import *
from false.markdown import *

EXTERNAL_LINKS = {
  "http://www.wikidata.org/wiki/\\1": re.compile("http://www.wikidata.org/entity/(.*)")
}

F = rdflib.Namespace("http://id.colourcountry.net/false/")
TEMP_IPFS = rdflib.Namespace("ipfs:/") # our temporary URI scheme
TRUE_IPFS = rdflib.Namespace("/ipfs/")

# TODO: put this per-context configuration into the graph
# note: "HTML" just means the output file format, FALSE doesn't care if it's HTML or not.
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
            logging.debug(f"Found cached blob for {path}")
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


def _get_page_tree(e_safe, ctx_safe, e_type, file_type):
    return [e_type.safe, ctx_safe, e_safe+'.'+file_type]

def get_page_path(e_safe, ctx_safe, e_type, output_dir, file_type='html'):
    return os.path.join(output_dir, *_get_page_tree(e_safe,ctx_safe,e_type,file_type))

def get_page_url(e_safe, ctx_safe, e_type, url_base, file_type='html'):
    return "/".join([url_base]+_get_page_tree(e_safe,ctx_safe,e_type,file_type))

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
        # logging.warning("Entities available: {e}".format(e="\n".join(sorted(tg.entities.keys()))))
        return ""

    if (tg.entities[src_safe], ctx) not in stage:
        if tg.entities[src_safe].isBlankNode():
            # an unstaged blank node is not an error
            return ""
        r = "can't resolve {src}@@{ctx}: not staged".format(src=src,ctx=ctx)
        logging.warning(r)
        return ""

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
    logging.debug(f"{e.id}: renditions available are {mt}")

    if rdflib.Literal('text/markdown') in mt:
        logging.debug(f"{e.id}: using markdown rendition {r.id}")
        content = ipfs_cat(r.id, cache_dir)
        return markdown_processor.convert(content.decode(get_charset(r, e, mt)))

    blobURL = r.blobURL.pick().id

    # FIXME: put this stuff into configuration and templates, not here

    if rdflib.Literal('text/html') in mt:
        # html is assumed to be a complete page
        logging.debug('{e}: using html rendition'.format(e=e.id))
        return '<div class="__embed__"><iframe src="{url}"></iframe></div>'.format(url=blobURL)

    for m in mt:
        if m.startswith('image/'):
            logging.debug(f"{e.id}: using {m} rendition")
            return '<img src="{url}">'.format(url=blobURL)

    for m in mt:
        if m.startswith('text/'):
            logging.debug(f"{e.id}: using {m} rendition")
            content = ipfs_cat(r.id, cache_dir)
            return content.decode("utf-8")

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
        try:
            uu = r.intendedUse
        except AttributeError:
            raise AttributeError(f"Rendition {r} didn't have an intended use! Check build phase, this shouldn't happen")
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

    logging.debug(f"{e.id}@@{ctx.id}: no suitable rendition")
    return ""

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
        autoescape=cfg.html_escape,
        trim_blocks=True,
        lstrip_blocks=True
    )
    jinja_e.globals["now"] = get_time_now

    markdown_processor = get_markdown_processor(tg,cfg)

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

                    dest = get_page_path(e_safe, ctx_safe, e_type, cfg.page_output_dir, cfg.page_file_type)
                    url = get_page_url(e_safe, ctx_safe, e_type, cfg.url_base, cfg.page_file_type)
                    e_types = None # found a renderable type
                    break

                if e_types is not None:
                    # get the next layer of types
                    e_types = e_types.get('rdfs_subClassOf')
                    logging.debug("{e}@@{ctx}: no template for direct type, trying {types}".format(e=e.id, ctx=ctx_id, types=repr(e_types)))

            if dest is None:
                logging.debug("{e}@@{ctx}: no template available".format(e=e.id, ctx=ctx_id))
                continue

            logging.debug(f'{e.id}@@{ctx_id}: will render as {e_type.id} -> {dest} ({url})')
            stage[(e, ctx_id)]=(tpl, dest)

            entities_to_write.add(e)
            if url in e:
              logging.debug(f"wtf using existing url {e.url}")

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
        next_write = {}

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
            logging.debug(f"Adding this inner html as {htmlProperty} to {e.id}@@{ctx_id}:\n{body[:100]}...")
            tg.add(e.id, htmlProperty, rdflib.Literal(body))

            try:
                content = e.render(tpl)
            except (jinja2.exceptions.UndefinedError, RequiredAttributeError) as err:
                # If an attribute is missing it may be a body for another entity/context that is not yet rendered
                logging.debug(f"{e.id}@@{ctx_id} not ready for {tpl}: {err}\nEntity is: {e.debug()}")
                next_write[(e, ctx_id)] = (err, traceback.format_exc())
                continue

            upgrade = lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, True)
            inline = lambda m: resolve_content_reference(m, tg, cfg.id_base, stage, e, False)

            try: # don't ask
                content = re.sub("<p>\s*<em>\s*<false-content([^>]*src=[^>]+)>\s*</false-content>\s*</em>\s*</p>", upgrade, content)
                content = re.sub("<p>\s*<em>\s*<false-content([^>]*src=[^>]+)>\s*</em>\s*</p>", upgrade, content)
                content = re.sub("<p>\s*<false-content([^>]*src=[^>]+)>\s*</false-content>\s*</p>", inline, content)
                content = re.sub("<p>\s*<false-content([^>]*src=[^>]+)>\s*</p>", inline, content)
                content = re.sub("<em>\s*<false-content([^>]*src=[^>]+)>\s*</false-content>\s*</em>", upgrade, content)
                content = re.sub("<em>\s*<false-content([^>]*src=[^>]+)>\s*</em>", upgrade, content)
                content = re.sub("<false-content([^>]*src=[^>]+)>\s*</false-content>", inline, content)
                content = re.sub("<false-content([^>]*src=[^>]+)>", inline, content)
                content = re.sub("<false-rescued([^>]*src=[^>]+)>", inline, content)
            except PublishNotReadyError as err:
                logging.debug("{e}@@{ctx} deferred: {err}".format(e=e.id, ctx=ctx_id, err=err))
                next_write[(e, ctx_id)] = (err, traceback.format_exc())
                continue

            os.makedirs(os.path.dirname(dest), exist_ok=True)
            logging.debug("{e}@@{ctx}: writing {dest}".format(e=e.id, ctx=ctx_id, dest=dest))
            with open(dest,'w') as f:
                f.write(content)

            progress = True

        to_write = next_write

    if to_write:
        err_list = []
        for item,error in to_write.items():
            err_list.append("{e}@@{ctx}: {err}".format(e=item[0].id, ctx=item[1], err=f"{error[0]}\n{error[1]}"))
        raise PublishError("{msg}\n     {detail}\n\n".format(msg=PUB_FAIL_MSG, detail='\n\n\n'.join(err_list)))
    else:
        logging.info("All written successfully.")

    open(os.path.join(cfg.page_output_dir,"index.html"),"wb").write(('''
<!DOCTYPE html>
<html>
  <head>
    <title>FALSE</title>
    <meta http-equiv="refresh" content="1; url='''+home_page+'''">
    <style type="text/css">
html, body { height: 100%; }
body { display: flex;
       align-items: center;
       justify-content: center;
       font-family: monospace; }
    </style>
  </head>
  <body>
<div>
<h1><a href="'''+home_page+'''">Continue to the site</a></h1>
<h2>Powered by FALSE</h2>
</div>
  </body>
</html>
''').encode("utf-8"))
    return home_page
