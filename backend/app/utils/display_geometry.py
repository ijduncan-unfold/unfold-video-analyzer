"""Bake display aspect into square pixels for browsers, JPEGs and vision models."""

VIEWING_SCALE = "scale=w='max(2,trunc(min(1280,720*dar)/2)*2)':h='max(2,trunc(ow/dar/2)*2)',setsar=1"
POSTER_SCALE = "scale=640:'max(2,trunc(640/dar/2)*2)',setsar=1"
