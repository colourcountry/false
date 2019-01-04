#!/bin/bash

export ASEMBED_SIZE=400
export ASPAGE_SIZE=1400

if [[ $2 == "-wipe" ]]
then
    find "$1" -name '.asEmbed.*' -or -name '.asPage.*' | while read EMBED
    do
        rm "$EMBED"
    done
fi

find "$1" -not -name '.*' -regextype posix-awk -regex '.*.(jpg|png)' | while read IMAGE
do
    export EMBED=$(dirname $IMAGE)/.asEmbed.$(basename $IMAGE)
    if [[ ! -f "$EMBED" ]]
    then
        convert -verbose -auto-orient -resize "$ASEMBED_SIZE"x"$ASEMBED_SIZE" "$IMAGE" "$EMBED"
    fi

    export PAGE=$(dirname $IMAGE)/.asPage.$(basename $IMAGE)
    if [[ ! -f "$PAGE" ]]
    then
        convert -verbose -auto-orient -resize "$ASPAGE_SIZE"x"$ASPAGE_SIZE" "$IMAGE" "$PAGE"
    fi
done
