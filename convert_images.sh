#!/bin/bash

export TEASER_WIDTH=400
export TEASER_HEIGHT=225
export EMBED_SIZE=800
export PAGE_SIZE=1200

if [[ $2 == "-wipe" ]]
then
    echo "Removing all cached conversions"
    find "$1" -name '.teaser.*'\
              -or -name '.embed.*'\
              -or -name '.page.*'\
              -or -name '.download.*' | while read FILE
    do
        rm "$FILE"
    done
fi

find "$1" -name '.download.ipfs-*' | while read FILE
do
    pushd $(dirname $FILE) > /dev/null
    export ORIG_FILE=$(basename "$FILE" | sed -e 's/[.]download[.]ipfs-[^.]*[.]//')
    if [[ -f "$ORIG_FILE" ]]
    then
        if [[ $2 != "-nochecks" ]]
        then
            echo "$ORIG_FILE: checking for changes"
            diff -q $(basename "$FILE") "$ORIG_FILE" || rm -fv .*."$ORIG_FILE"
        fi
    else
        echo "$ORIG_FILE has been removed."
        rm -fv .*."$ORIG_FILE"
    fi
    popd > /dev/null
done

find "$1" -not -name '.*'\
          -not -name 'teaser.*.*'\
          -not -name 'embed.*.*'\
          -not -name 'page.*.*'\
          -regextype posix-awk -regex '.*.(jpg|png)' | while read IMAGE
do
    pushd $(dirname $IMAGE) > /dev/null
    export B=$(basename $IMAGE)

    if find -type f -name '.download.*.'"$B" -exec false {} +
    then
        cp "$B" .download.ipfs-$(ipfs add -Q "$B")."$B"
    fi
    if find -type f -name '.teaser.*.'"$B" -exec false {} +
    then
        convert -verbose -auto-orient -resize "$TEASER_WIDTH"x"$TEASER_HEIGHT"^ -gravity center -crop "$TEASER_WIDTH"x"$TEASER_HEIGHT"+0+0 "$B" .teaser.tmp
        mv .teaser.tmp .teaser.ipfs-$(ipfs add -Q .teaser.tmp)."$B"
    fi

    if find -type f -name '.embed.*.'"$B" -exec false {} +
    then
        convert -verbose -auto-orient -resize "$EMBED_SIZE"x"$EMBED_SIZE"\> "$B" .embed.tmp
        mv .embed.tmp .embed.ipfs-$(ipfs add -Q .embed.tmp)."$B"
    fi

    if find -type f -name '.page.*.'"$B" -exec false {} +
    then
        convert -verbose -auto-orient -resize "$PAGE_SIZE"x"$PAGE_SIZE"\> "$B" .page.tmp
        mv .page.tmp .page.ipfs-$(ipfs add -Q .page.tmp)."$B"
    fi
    popd > /dev/null
done
