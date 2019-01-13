#!/bin/bash

export TEASER_SIZE=400
export EMBED_SIZE=800
export PAGE_SIZE=1200

if [[ $2 == "-wipe" ]]
then
    find "$1" -name '.teaser.*' -or -name '.embed.*' -or -name '.page.*' | while read FILE
    do
        rm "$FILE"
    done
fi

find "$1" -not -name '.*' -regextype posix-awk -regex '.*.(jpg|png)' | while read IMAGE
do
    export TEASER=$(dirname $IMAGE)/.teaser.$(basename $IMAGE)
    if [[ ! -f "$TEASER" ]]
    then
        convert -verbose -auto-orient -resize "$TEASER_SIZE"x"$TEASER_SIZE"\> "$IMAGE" "$TEASER"
    fi

    export EMBED=$(dirname $IMAGE)/.embed.$(basename $IMAGE)
    if [[ ! -f "$EMBED" ]]
    then
        convert -verbose -auto-orient -resize "$EMBED_SIZE"x"$EMBED_SIZE"\> "$IMAGE" "$EMBED"
    fi

    export PAGE=$(dirname $IMAGE)/.page.$(basename $IMAGE)
    if [[ ! -f "$PAGE" ]]
    then
        convert -verbose -auto-orient -resize "$PAGE_SIZE"x"$PAGE_SIZE"\> "$IMAGE" "$PAGE"
    fi
done
