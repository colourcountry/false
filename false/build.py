#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import logging, os, datetime

F = rdflib.Namespace("http://www.colourcountry.net/false/model/")

FILE_TYPES = { ".html": "text/html",
               ".txt": "text/plain",
               ".md": "text/markdown",
               ".jpg": "image/jpeg",
               ".png": "image/png" }

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
    g.add((doc_id, F.rendition, blob_id))
    return blob_id


def build_graph(g, ipfs_client, ipfs_namespace, source_dir):
    gg = rdflib.Graph()
    gg.bind('f', F)
    gg.bind('dc', DC)
    gg.bind('skos', SKOS)
    gg.bind('owl', OWL)
    gg.bind('ipfs', ipfs_namespace)

    documents = {}
    entities = {}

    for s, p, o in g:
        if s not in entities:
            entities[s] = s
        if p == RDF.type:
            if o == F.Document:
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
        else:
            gg.add((entities[s], p, o))

    for s, doc_id in documents.items():
        g.add((doc_id, F.published, rdflib.Literal(datetime.datetime.now().isoformat())))

        for t, mime in FILE_TYPES.items():
            fn = os.path.basename(s+t)
            try:
                o = open(os.path.join(source_dir,fn),'rb').read()
                # TODO: refactor these additions to the graph and save out the full RDF
                blob_id = add_rendition(gg, documents[s], o, ipfs_client, ipfs_namespace,
                    mediaType=rdflib.Literal(mime),
                    charset=rdflib.Literal('utf-8') # let's hope
                    )
            except IOError:
                logging.warn("Couldn't open %s" % fn)
    return gg

