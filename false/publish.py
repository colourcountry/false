#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, re, urllib.parse
import ipfsapi
import jinja2, markdown
import pprint
from triplate import *

EXTERNAL_LINKS = {
  "http://www.wikidata.org/wiki/\\1": re.compile("http://www.wikidata.org/entity/(.*)")
}

class EmbedNotReady(Exception):
    pass

F = rdflib.Namespace("http://www.colourcountry.net/false/model/")

def get_page_path(e, e_type, output_dir, file_type='html'):
    return os.path.join(output_dir, e_type.safe, e+'.'+file_type)

def get_page_url(e, e_type, url_base, file_type='html'):
    return '%s/%s/%s' % (url_base, e_type.safe, e+'.'+file_type)

def resolve_embed(m, tg, e_safe, in_p=False):
    logging.debug("Resolving img embed %s" % (m.group(1)))
    attrs = {}
    for mm in re.finditer('(\S+)="([^"]*)"', m.group(1)):
        attrs[mm.group(1)]=mm.group(2)

    logging.debug(attrs)

    # FIXME: better way to match data-final as attr without a value
    if "data-final" in attrs or "data-final" in re.sub('(\S+)="([^"]*)"', '', m.group(1)):
        logging.debug("Retained final img %s" % (m.group(1)))
        return m.group(0)

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

def get_embed_html_for_rendition(tg, e, r, ipfs_client):
    mt = r.f_mediaType
    enc = r.f_charset.pop()
    logging.info('%s: found %s rendition' % (e, mt))

    if rdflib.Literal('text/html') in mt:
        return ipfs_client.cat(r.id).decode(enc)

    if rdflib.Literal('text/markdown') in mt:
        md = markdown.Markdown(output_format="html5")
        return md.convert(ipfs_client.cat(r.id).decode(enc))

    for t in mt:
        if t.startswith('image/'):
            url = tg.entities[e].f_url
            if url:
                # FIXME: hard coded caption
                return '<a href="%s"><img data-final title="%s" alt="%s" src="%s"></a>' % (url, "Image information available", tg.entities[e].f_description, r.f_blob_url)
            else:
                return '<img data-final alt="%s" src="%s">' % (tg.entities[e].f_description, r.f_blob_url)

    logging.debug("%s: media type %s can't be embedded" % (e, mt))
    return None

def get_embed_html(tg, e, ipfs_client):
    available = tg.entities[e].f_rendition

    for r in available:
        if F.embedContext in r.f_targetContext:
            eh = get_embed_html_for_rendition(tg, e, r, ipfs_client)
            if eh:
                return eh
            available.pop(r)

    for r in available:
        eh = get_embed_html_for_rendition(tg, e, r, ipfs_client)
        if eh:
            return eh
        available.pop(r)

    logging.debug("%s: no renditions, so can't be embedded" % e)
    return None

def publish(g, template_dir, output_dir, url_base, ipfs_client):
    tg = TemplatableGraph(g)

    jinja_e = jinja2.Environment(loader=jinja2.FileSystemLoader('templates'))

    embed_html = {}
    stage = {}

    for e in tg.entities:

        # use the most direct type because we need to go up in a specific order
        # FIXME: provide ordered walk functions on entities?
        e_types = tg.entities[e].type()
        e_id = tg.entities[e].id

        has_url = False
        for match in tg.entities[e].skos_exactMatch:
            for repl, pattern in EXTERNAL_LINKS.items():
                logging.warn(match)
                if pattern.match(str(match)):
                    url = pattern.sub(repl, str(match))
                    tg.add(e_id, F.url, rdflib.Literal(url))
                    has_url = True

        if has_url:
            continue # don't need to render

        while e_types:
            for e_type in e_types:
                try:
                    t = jinja_e.get_template(e_type.safe)
                except IOError as err:
                    logging.debug("%s: no template %s" % (e, e_type.safe))
                    continue

                dest = get_page_path(e, e_type, output_dir)
                url = get_page_url(e, e_type, url_base)

                logging.info('%s: will render as %s -> %s' % (e, e_type, dest))
                stage[dest] = (t, e)
                e_types = None # found a renderable type

                # add triples for the template to pick up
                # we can't add the url to the graph as it depends on what templates were available.
                tg.add(e_id, F.url, rdflib.Literal(url))

                # FIXME: figure out embeds while building the graph, and use that to line everything up
                eh = get_embed_html(tg, e, ipfs_client)
                if eh:
                    embed_html[e] = eh
                break


            if e_types is not None:
                # get the next layer of types
                e_types = e_types.rdfs_subClassOf

    logging.info("Finding embeds")

    while len(embed_html) > 0:
        progress = set()
        for e,html in embed_html.items():
            try:
                html = re.sub("<p>\s*<img([^>]*)>\s*</p>", lambda m: resolve_embed(m, tg, e, True), html)
                html = re.sub("<img([^>]*)>", lambda m: resolve_embed(m, tg, e, False), html)
                logging.debug("%s: all embeds resolved" % tg.entities[e])
                tg.add(tg.entities[e].id, F.html, html)
                progress.add(e)
            except EmbedNotReady:
                continue

        if len(progress) == 0:
            raise RuntimeError("Embed loop in %s" % ','.join(embed_html.keys()))

        for e in progress:
            del(embed_html[e])

    logging.info("Stage is ready")

    for dest in stage:
        t, e = stage[dest]
        content = t.render(tg.entities[e].po)

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        logging.info("%s: writing %s" % (e, dest))
        with open(dest,'w') as f:
            f.write(content)


if __name__=="__main__":
    g = rdflib.Graph()
    for path, dirs, files in os.walk(FALSE_SRC):
      for f in files:
          if f.endswith('.ttl'):
              logging.info("Loading %s from %s" % (f,path))
              g.load(os.path.join(path,f), format='ttl')
    publish(build_graph(g))
