#!/usr/bin/python3

import false.publish, false.build, false.config
import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, re, urllib.parse, datetime
import ipfsapi
import jinja2, markdown
import pprint

logging.basicConfig(level=logging.DEBUG)

if __name__=="__main__":

    cfg = false.config.Config( src_dir=os.environ["FALSE_SRC"],
                          url_base=os.environ["FALSE_URL_BASE"],
                          output_dir=os.environ["FALSE_OUT"],
                          template_dir=os.environ["FALSE_TEMPLATES"],
                          home_site=os.environ["FALSE_HOME_SITE"],
                          id_base=os.environ["FALSE_ID_BASE"])

    ipfs_namespace = rdflib.Namespace("/ipfs/")

    try:
        ipfs_client = ipfsapi.connect('127.0.0.1',5001)
        cfg.setIPFS(ipfs_client, ipfs_namespace, "ipfs")
    except ipfsapi.exceptions.ConnectionError:
        logging.info("No IPFS daemon running. Trying to go ahead with mockipfs")

        import false.mockipfs
        ipfs_client = false.mockipfs.MockIPFS("ipfs")
        cfg.setIPFS(ipfs_client, ipfs_namespace, "ipfs")

    cfg.validate()

    g = rdflib.Graph()
    g.load(os.path.join(os.path.dirname(false.build.__file__),"false.ttl"), format='ttl')
    g.load(os.path.join(os.path.dirname(false.build.__file__),"false-xl.ttl"), format='ttl')

    for path, dirs, files in os.walk(cfg.src_dir):
        for f in files:
            if f.endswith('.ttl'):
                logging.info("Loading %s from %s" % (f,path))
                g.load(os.path.join(path,f), format='ttl', publicID=cfg.id_base)

    logging.info("** Building **")

    final_graph = false.build.build_graph(g, cfg)

    logging.info("** Publishing **")

    home_page = false.publish.publish_graph(final_graph, cfg)

    print(home_page)
