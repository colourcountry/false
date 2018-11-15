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
               "image/png": ".png" }

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

def add_rendition(g, doc_id, blob, ipfs_client, ipfs_namespace, mediaType, **properties):
    if not ipfs_client:
        logging.warning("No IPFS, can't add rendition info to document %s" % doc_id)
        return None

    blob_hash = ipfs_client.add_bytes(blob)

    doc_basename = posixpath.basename(doc_id)
    if doc_basename:
        blob_filename = doc_basename+FILE_TYPES[str(mediaType)]
    else:
        blob_filename = "blob"+FILE_TYPES[str(mediaType)]

    info = rdflib.Graph()
    info.bind('', F)
    for p, o in g[doc_id]:
        info.add((doc_id, p, o))
    info.add((doc_id, F.rendition, rdflib.URIRef(blob_filename))) # relative path to the file, as we don't know the hash
    info_blob = info.serialize(format='ttl')
    logging.debug(info_blob)

    ipld = {"Links": [{"Name": blob_filename, "Hash": blob_hash, "Size": len(blob)}],
            "Data": "\u0008\u0001"} # this data seems to be required for something to be a directory

    if info_blob:
        info_hash = ipfs_client.add_bytes(info_blob)
        ipld["Links"].append({"Name": "info.ttl", "Hash": info_hash, "Size": len(info_blob)})

    ipld_blob = json.dumps(ipld).encode('utf-8')
    logging.debug(ipld_blob)
    wrapper_resp = ipfs_client.object_put(io.BytesIO(ipld_blob))

    wrapped_id = ipfs_namespace["%s/%s" % (wrapper_resp["Hash"], blob_filename)]
    g.add((wrapped_id, RDF.type, F.Media))
    g.add((wrapped_id, F.mediaType, mediaType))
    g.add((wrapped_id, F.blobURL, wrapped_id))

    for k, v in properties.items():
        logging.debug("%s: adding property %s=%s" % (doc_id, k, v))
        g.add((wrapped_id, F[k], v))
    logging.info("%s: adding rendition %s" % (doc_id, wrapped_id))
    g.add((doc_id, F.rendition, wrapped_id))
    return wrapped_id


def build_graph(g, ipfs_client, ipfs_namespace, source_dir, id_base):
    gg = rdflib.Graph()
    gg.bind('', F)
    gg.bind('dc', DC)
    gg.bind('skos', SKOS)
    gg.bind('owl', OWL)
    gg.bind('rdf', RDF)
    gg.bind('rdfs', RDFS)
    gg.bind('ipfs', ipfs_namespace)
    gg.bind('http', HTTP) # this makes URLs look a bit nicer but it relies on namespace bindings being processed in this order
    gg.bind('https', HTTPS)

    doc_types = [x[0] for x in g.query("""select ?t where { ?t rdfs:subClassOf+ :Content }""")]

    documents = {}
    entities = {}
    mdproc = markdown.Markdown(extensions=[ImgExtExtension(base=id_base)])

    for s, p, o in g:
        if s not in entities:
            entities[s] = s
        if p == RDF.type:
            if o == F.Document or o in doc_types:
                entities[s] = s
                documents[s] = s

    for s, p, o in g:
        if o in entities:
            o = entities[o]

        if p == F.markdown:
            blob_id = add_rendition(gg, documents[s], o.encode('utf-8'), ipfs_client, ipfs_namespace,
                mediaType=rdflib.Literal('text/markdown'),
                charset=rdflib.Literal('utf-8'),
            )

            html = mdproc.convert(o)
            for url in mdproc.images:
                uriref = rdflib.URIRef(url)
                if uriref in documents:
                    logging.debug("%s: found embed of %s" % (documents[s], url))
                    gg.add((documents[s], F.includes, uriref))
                elif uriref in entities:
                    raise ValidationError("%s: tried to embed non-document %s" % (documents[s], url))
                else:
                    logging.debug("%s: found embed of Web document %s" % (documents[s], url))

            for url in mdproc.links:
                uriref = rdflib.URIRef(url)
                if uriref in documents:
                    logging.debug("%s: found link to %s" % (documents[s], url))
                    gg.add((documents[s], F.links, uriref))
                else:
                    logging.info("%s: found mention of %s" % (documents[s], url))
                    gg.add((documents[s], F.mentions, uriref))

        else:
            gg.add((entities[s], p, o))

    for s, doc_id in documents.items():
        gg.add((doc_id, F.published, rdflib.Literal(datetime.datetime.now().isoformat(), datatype=XSD.dateTime)))

        for mime, t in FILE_TYPES.items():
            fn = os.path.basename(s+t)
            try:
                blob = open(os.path.join(source_dir,fn),'rb').read()
                # TODO: refactor these additions to the graph and save out the full RDF
                blob_id = add_rendition(gg, doc_id, blob, ipfs_client, ipfs_namespace,
                    mediaType=rdflib.Literal(mime),
                    charset=rdflib.Literal("utf-8"),
                    )

                if mime == 'text/markdown':
                    html = mdproc.convert(blob.decode('utf-8'))
                    for url in mdproc.images:
                        uriref = rdflib.URIRef(url)
                        if uriref in documents:
                            logging.debug("%s: found link to %s" % (doc_id, url))
                            gg.add((doc_id, F.includes, uriref))
                        else:
                            logging.warning("%s: found link to non-document %s %s" % (doc_id, url, documents))

            except IOError:
                pass

    return gg
