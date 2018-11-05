#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import logging, os, re, datetime, markdown

F = rdflib.Namespace("http://www.colourcountry.net/false/model/")

FILE_TYPES = { ".html": "text/html",
               ".txt": "text/plain",
               ".md": "text/markdown",
               ".jpg": "image/jpeg",
               ".png": "image/png" }

# h/t https://stackoverflow.com/questions/29259912/how-can-i-get-a-list-of-image-urls-from-a-markdown-file-in-python
class ImgExtractor(markdown.treeprocessors.Treeprocessor):
    def run(self, doc):
        "Find all images and append to markdown.images. "
        self.markdown.images = []
        for image in doc.findall('.//img'):
            self.markdown.images.append(image.get('src'))

class ImgExtExtension(markdown.extensions.Extension):
    def extendMarkdown(self, md, md_globals):
        img_ext = ImgExtractor(md)
        md.treeprocessors.add('imgext', img_ext, '>inline')

def add_rendition(g, doc_id, blob, ipfs_client, ipfs_namespace, **properties):
    if not ipfs_client:
        logging.warn("No IPFS, can't add rendition info to document %s" % doc_id)
        return None

    r = ipfs_client.add_bytes(blob)
    blob_id = ipfs_namespace[r]
    g.add((blob_id, RDF.type, F.Media))
    g.add((blob_id, F.blob_url, blob_id))
    for k, v in properties.items():
        g.add((blob_id, F[k], v))
    logging.info("%s: adding rendition %s" % (doc_id, blob_id))
    g.add((doc_id, F.rendition, blob_id))
    return blob_id


def build_graph(g, ipfs_client, ipfs_namespace, source_dir):
    gg = rdflib.Graph()
    gg.bind('f', F)
    gg.bind('dc', DC)
    gg.bind('skos', SKOS)
    gg.bind('owl', OWL)
    gg.bind('ipfs', ipfs_namespace)

    doc_types = [x[0] for x in g.query("""select ?t where { ?t rdfs:subClassOf+ f:Content }""")]

    documents = {}
    entities = {}
    mdproc = markdown.Markdown(extensions=[ImgExtExtension()])

    for s, p, o in g:
        if s not in entities:
            entities[s] = s
        if p == RDF.type:
            logging.warn("%s %s %s" % (list(doc_types), o, o in doc_types))
            if o == F.Document or o in doc_types:
                entities[s] = s
                documents[s] = s

    for s, p, o in g:
        if o in entities:
            o = entities[o]

        if p == F.markdown:
            blob_id = add_rendition(gg, documents[s], o.encode('utf-8'), ipfs_client, ipfs_namespace,
                mediaType=rdflib.Literal('text/markdown'),
                charset=rdflib.Literal('utf-8')
            )

            find_markdown_embeds(mdproc, gg, documents[s], o)
        else:
            gg.add((entities[s], p, o))

    for s, doc_id in documents.items():
        g.add((doc_id, F.published, rdflib.Literal(datetime.datetime.now().isoformat())))

        for t, mime in FILE_TYPES.items():
            fn = os.path.basename(s+t)
            try:
                blob = open(os.path.join(source_dir,fn),'rb').read()
                # TODO: refactor these additions to the graph and save out the full RDF
                blob_id = add_rendition(gg, doc_id, blob, ipfs_client, ipfs_namespace,
                    mediaType=rdflib.Literal(mime),
                    charset=rdflib.Literal('utf-8') # let's hope
                    )

                if mime == 'text/markdown':
                    find_markdown_embeds(mdproc, gg, doc_id, blob.decode('utf-8'))

            except IOError:
                pass

    return gg

def find_markdown_embeds(mdproc, g, doc_id, content):
    html = mdproc.convert(content)
    for url in mdproc.images:
        logging.warn("%s: found link to %s" % (doc_id, url))
        g.add((doc_id, F.uses, rdflib.URIRef(url)))
