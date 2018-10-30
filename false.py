#!/usr/bin/python3

import false.publish, false.build
import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, mockipfs, re, urllib.parse, datetime
import ipfsapi
import jinja2, markdown
import pprint
from triplate import *

logging.basicConfig(level=logging.DEBUG)

FALSE_SRC = os.environ["FALSE_SRC"]
FALSE_URL_BASE = os.environ["FALSE_URL_BASE"]
FALSE_OUT = os.environ["FALSE_OUT"]
FALSE_TEMPLATES = os.environ["FALSE_TEMPLATES"]

if __name__=="__main__":
    g = rdflib.Graph()
    for path, dirs, files in os.walk(FALSE_SRC):
      for f in files:
          if f.endswith('.ttl'):
              logging.info("Loading %s from %s" % (f,path))
              g.load(os.path.join(path,f), format='ttl')

    try:
        ipfs_client = ipfsapi.connect('127.0.0.1',5001)
        ipfs_namespace = rdflib.Namespace("/ipfs/")
    except ipfsapi.exceptions.ConnectionError:
        logging.warn("No IPFS daemon running.")
        ipfs_client = mockipfs.MockIPFS(FALSE_OUT, FALSE_URL_BASE)
        ipfs_namespace = ipfs_client.namespace

    logging.info("** Building **")

    final_graph = false.build.build_graph(g,
        ipfs_client, ipfs_namespace,
        FALSE_SRC)

    logging.info("** Publishing **")

    false.publish.publish(final_graph, FALSE_TEMPLATES, FALSE_OUT, FALSE_URL_BASE, ipfs_client)
