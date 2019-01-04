#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, RDFS, DC, SKOS, OWL, XSD
import logging, os, re, io, datetime, markdown, urllib.parse, json, posixpath

F = rdflib.Namespace("http://id.colourcountry.net/false/")
HTTP = rdflib.Namespace("http://")
HTTPS = rdflib.Namespace("https://")


FILE_TYPES = { "text/html": ".html",
               "text/plain": ".txt",
               "text/markdown": ".md",
               "image/jpeg": ".jpg",
               "image/png": ".png",
               "application/pdf": ".pdf" }

CONTEXTS = { ".asEmbed.": F.asEmbed,
             ".asPage.": F.asPage,
             "": F.asDownload }

CONTENT_NEW = 0
CONTENT_READY = 1
CONTENT_EXTERNAL = 2

class ValidationError(ValueError):
    pass

# h/t https://stackoverflow.com/questions/29259912/how-can-i-get-a-list-of-image-urls-from-a-markdown-file-in-python
class ImgExtractor(markdown.treeprocessors.Treeprocessor):
    def __init__(self, md, base):
        self.base = base
        super(ImgExtractor, self).__init__(md)

    def run(self, doc):
        "Find all images and links and append to markdown.images. "
        self.markdown.images = []
        self.markdown.links = []
        for image in doc.findall('.//img'):
            self.markdown.images.append(urllib.parse.urljoin(self.base, image.get('src')))
        for image in doc.findall('.//a'):
            self.markdown.links.append(urllib.parse.urljoin(self.base, image.get('href')))

class ImgExtExtension(markdown.extensions.Extension):
    def __init__(self, **kwargs):
        self.config = {'base' : ['http://example.org/', 'The base URI to use when embedded content is specified as a relative URL']}
        super(ImgExtExtension, self).__init__(**kwargs)

    def extendMarkdown(self, md, md_globals):
        img_ext = ImgExtractor(md, self.getConfig('base'))
        md.treeprocessors.add('imgext', img_ext, '>inline')

def url_to_path(url, path='', pfx='', sfx=''):
    if not url:
        return path
    p, s = posixpath.split(url)
    if path:
        return url_to_path(p, os.path.join(pfx+s+sfx, path))
    else:
        return url_to_path(p, pfx+s+sfx)

def add_rendition(g, content_id, blob, ipfs_client, ipfs_namespace, mediaType, **properties):
    if not ipfs_client:
        logging.warning("No IPFS, can't add rendition info to document %s" % content_id)
        return None

    blob_hash = ipfs_client.add_bytes(blob)

    doc_basename = posixpath.basename(content_id)
    if doc_basename:
        blob_filename = doc_basename+FILE_TYPES[str(mediaType)]
    else:
        blob_filename = "blob"+FILE_TYPES[str(mediaType)]

    # TODO: spot blank nodes and remove the filename (since it's not guaranteed to be stable)

    info = rdflib.Graph()
    info.bind('', F)
    for p, o in g[content_id]:
        info.add((content_id, p, o))
    info.add((content_id, F.rendition, rdflib.URIRef(blob_filename))) # relative path to the file, as we don't know the hash
    info_blob = info.serialize(format='ttl')

    ipld = {"Links": [{"Name": blob_filename, "Hash": blob_hash, "Size": len(blob)}],
            "Data": "\u0008\u0001"} # this data seems to be required for something to be a directory

    if info_blob:
        info_hash = ipfs_client.add_bytes(info_blob)
        ipld["Links"].append({"Name": "info.ttl", "Hash": info_hash, "Size": len(info_blob)})

    ipld_blob = json.dumps(ipld).encode('utf-8')
    wrapper_resp = ipfs_client.object_put(io.BytesIO(ipld_blob))

    wrapped_id = ipfs_namespace["%s/%s" % (wrapper_resp["Hash"], blob_filename)]
    g.add((wrapped_id, RDF.type, F.Media))
    g.add((wrapped_id, F.mediaType, mediaType))
    g.add((wrapped_id, F.blobURL, wrapped_id))

    for k, v in properties.items():
        logging.debug("%s: adding property %s=%s" % (content_id, k, v))
        g.add((wrapped_id, F[k], v))
    logging.info("%s: adding rendition %s" % (content_id, wrapped_id))
    g.add((content_id, F.rendition, wrapped_id))
    return wrapped_id

def build_graph(g, cfg):
    gg = rdflib.Graph()
    gg.bind('', F)
    gg.bind('dc', DC)
    gg.bind('skos', SKOS)
    gg.bind('owl', OWL)
    gg.bind('rdf', RDF)
    gg.bind('rdfs', RDFS)
    gg.bind('ipfs', cfg.ipfs_namespace)

    doc_types = [x[0] for x in g.query("""select ?t where { ?t rdfs:subClassOf+ :Content }""")]

    content = {}
    entities = {}
    mdproc = markdown.Markdown(extensions=[ImgExtExtension(base=cfg.id_base)])

    for s, p, o in g:
        if s not in entities:
            entities[s] = s
        if p == RDF.type:
            if o == F.Content or o in doc_types:
                logging.debug("Found content %s" % s)
                entities[s] = s
                content[s] = CONTENT_NEW

    for s, p, o in g:
        if o in entities:
            o = entities[o]

        if p == F.markdown:
            if s not in content:
                raise ValidationError("%s: entity has `markdown` property but is not any of the defined document types (%s %s)" % (s, F.Content, " ".join(doc_types)))
            blob_id = add_rendition(gg, s, o.encode('utf-8'), cfg.ipfs_client, cfg.ipfs_namespace,
                mediaType=rdflib.Literal('text/markdown'),
                charset=rdflib.Literal('utf-8'),
                intendedUse=F.asPage # embeds will fall back to this, but it can be distinguished from stuff to offer as a download
            )
            content[s] = CONTENT_READY

            html = mdproc.convert(o)
            for url in mdproc.images:
                uriref = rdflib.URIRef(url)
                if uriref in content:
                    logging.debug("%s: found embed of %s" % (s, url))
                    gg.add((s, F.includes, uriref))
                elif uriref in entities:
                    raise ValidationError("%s: tried to embed non-document %s" % (s, url))
                else:
                    logging.debug("%s: found embed of Web document %s" % (s, url))

            for url in mdproc.links:
                uriref = rdflib.URIRef(url)
                if uriref in content:
                    logging.debug("%s: found link to %s" % (s, url))
                    gg.add((s, F.links, uriref))
                elif uriref in entities:
                    logging.info("%s: found mention of %s" % (s, url))
                    gg.add((s, F.mentions, uriref))
                else:
                    logging.debug("%s: found link to Web document %s" % (s, url))

        else:
            gg.add((s, p, o))

    for content_id in content:
        gg.add((content_id, F.published, rdflib.Literal(datetime.datetime.now().isoformat(), datatype=XSD.dateTime)))

        if not content_id.startswith(cfg.id_base):
            content[content_id] = CONTENT_EXTERNAL
            continue # id is not ours, so nowhere to pull files from

        for pfx, ctx in CONTEXTS.items():
            found_something = False
            for mime, ext in FILE_TYPES.items():
                fn = url_to_path(content_id[len(cfg.id_base):], pfx=pfx, sfx=ext)
                try:
                    blob = open(os.path.join(cfg.src_dir,fn),'rb').read()
                    # TODO: refactor these additions to the graph and save out the full RDF
                    blob_id = add_rendition(gg, content_id, blob, cfg.ipfs_client, cfg.ipfs_namespace,
                        mediaType=rdflib.Literal(mime),
                        charset=rdflib.Literal("utf-8"),
                        intendedUse=ctx
                        )
                    content[content_id] = CONTENT_READY # we've got something, even if it may be intended for a different context

                    if mime == 'text/markdown':
                        html = mdproc.convert(blob.decode('utf-8'))
                        for url in mdproc.images:
                            uriref = rdflib.URIRef(url)
                            if uriref in content:
                                logging.debug("%s: found link to %s" % (content_id, url))
                                gg.add((content_id, F.includes, uriref))
                            else:
                                logging.warning("%s: found link to non-document %s %s" % (content_id, url, content))

                except IOError:
                    continue

            logging.debug("%s: looking for %s" % (content_id, pfx))

    for content_id in content:
        if content[content_id] not in (CONTENT_READY, CONTENT_EXTERNAL):
            raise ValueError("%s: no renditions available, cannot publish" % content_id)

    return gg
