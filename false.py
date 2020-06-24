#!/usr/bin/python3

import false.publish, false.publish_media, false.build, false.config
import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, re, urllib.parse, datetime
import jinja2, markdown
import pprint

log_handlers=[logging.StreamHandler()]
log_handlers[0].setLevel(logging.INFO)
try:
    log_handlers.append(logging.FileHandler(os.environ["FALSE_LOG_FILE"]))
except KeyError:
    pass
logging.basicConfig(level=logging.DEBUG,handlers=log_handlers)

if __name__=="__main__":

    logging.info(f"*** Started FALSE at {datetime.datetime.now().isoformat()} ***")

    cfg = false.config.Config(
                          url_base=os.environ["FALSE_URL_BASE"],
                          output_dir=os.environ["FALSE_OUT"],
                          template_dir=os.environ["FALSE_TEMPLATES"],
                          home_site=os.environ["FALSE_HOME_SITE"],
                          id_base=os.environ["FALSE_ID_BASE"],
                          work_dir=os.environ["FALSE_WORK_DIR"])

    b = false.build.Builder(cfg.work_dir, cfg.id_base)
    b.add_ttl(os.path.join(os.path.dirname(false.build.__file__),"false.ttl"))
    b.add_ttl(os.path.join(os.path.dirname(false.build.__file__),"false-xl.ttl"))
    b.add_dir(os.environ["FALSE_SRC"])
    g = b.build()

    g.serialize(destination=os.path.join(os.environ["FALSE_WORK_DIR"],"__result.ttl"), format="ttl")

    logging.info("** Publishing media **")

    # Copy media files into the publish area (via IPFS or directly)
    # and remove local paths
    false.publish_media.publish_media(g, cfg.output_dir)

    logging.info("** Publishing graph **")

    # Build HTML pages
    home_page = false.publish.publish_graph(g, cfg)

    g.serialize(destination=os.path.join(cfg.output_dir,"site.ttl"),format="ttl")

    print(home_page)
