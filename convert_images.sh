#!/bin/bash

echo "Looking in '$SRC_DIR' for blobs to render to '$BLOB_DIR'"
echo "- rendering & cropping teasers to $TEASER_WIDTH x $TEASER_HEIGHT px"
echo "- rendering embeds to $EMBED_SIZE px"
echo "- rendering pages to $PAGE_SIZE px"

mkdir -p "$BLOB_DIR"
pushd "$BLOB_DIR"
export FULL_BLOB_DIR=$(pwd)
popd > /dev/null

pushd "$SRC_DIR"
find .    -not -name '.ttl'\
          -not -name 'teaser.*.*'\
          -not -name 'embed.*.*'\
          -not -name 'page.*.*'\
          -regextype posix-awk -regex '.*.(jpg|png)' | while read IMAGE
do
    export D=$(dirname $IMAGE)
    export B=$(basename $IMAGE)
    echo "$D -- $B"

    if find "$FULL_BLOB_DIR" -type f -name 'download.*.'"$B" -exec false {} +
    then
        mkdir -p "$FULL_BLOB_DIR"/"$D"
        cp "$IMAGE" "$FULL_BLOB_DIR"/"$D"/download.unknown."$B"
    fi
    #if find "$BLOB_DIR" -type f -name 'teaser.*.'"$B" -exec false {} +
    #then
    #    convert -verbose -auto-orient -resize "$TEASER_WIDTH"x"$TEASER_HEIGHT"^ -gravity center -crop "$TEASER_WIDTH"x"$TEASER_HEIGHT"+0+0 "$B" .teaser.tmp
    #    mv .teaser.tmp "$ORIG_DIR/$BLOB_DIR/.teaser.ipfs-$(ipfs add -Q .teaser.tmp)."$B"
    #fi

    #if find -type f -name '.embed.*.'"$B" -exec false {} +
    #then
    #    convert -verbose -auto-orient -resize "$EMBED_SIZE"x"$EMBED_SIZE"\> "$B" .embed.tmp
    #    mv .embed.tmp .embed.ipfs-$(ipfs add -Q .embed.tmp)."$B"
    #fi

    #if find -type f -name '.page.*.'"$B" -exec false {} +
    #then
    #    convert -verbose -auto-orient -resize "$PAGE_SIZE"x"$PAGE_SIZE"\> "$B" .page.tmp
    #    mv .page.tmp .page.ipfs-$(ipfs add -Q .page.tmp)."$B"
    #fi
done
popd > /dev/null

pushd "$FULL_BLOB_DIR"
find . -name "*.unknown.*" | while read FILE
do
  export IPFS_HASH=$(ipfs add -Q "$FILE")

  if [ -n "$IPFS_HASH" ]
  then
    export D=$(dirname "$FILE")
    export B=$(basename "$FILE")
    mv "$FILE" "$D"/$(echo "$B" | sed -e s/[.]unknown[.]/.ipfs-"$IPFS_HASH"./)
  fi
done
popd > /dev/null
