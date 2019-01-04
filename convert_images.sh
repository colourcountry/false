#!/bin/bash

export EMBED_SIZE=400
export PAGE_SIZE=1400

if [[ $2 == "-wipe" ]]
then
    find "$1" -name '.embed.*' -or -name '.page.*' | while read EMBED
    do
        rm "$EMBED"
    done
fi

find "$1" -not -name '.*' -regextype posix-awk -regex '.*.(jpg|png)' | while read IMAGE
do
    export EMBED=$(dirname $IMAGE)/.embed.$(basename $IMAGE)
    if [[ ! -f "$EMBED" ]]
    then
        convert -verbose -auto-orient -resize "$EMBED_SIZE"x"$EMBED_SIZE" "$IMAGE" "$EMBED"
    fi

    export PAGE=$(dirname $IMAGE)/.page.$(basename $IMAGE)
    if [[ ! -f "$PAGE" ]]
    then
        convert -verbose -auto-orient -resize "$PAGE_SIZE"x"$PAGE_SIZE" "$IMAGE" "$PAGE"
    fi
done
