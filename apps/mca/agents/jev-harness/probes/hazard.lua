-- What is under, ahead of, and ahead-below the player. The facing vector comes
-- from MC's yaw convention: 0 = +Z, 90 = -X.
local rad = math.rad(api:yaw())
local fx, fz = -math.sin(rad), math.cos(rad)
local bx, by, bz = api:blockX(), api:blockY(), api:blockZ()
local ax = bx + math.floor(fx * 1.5 + 0.5)
local az = bz + math.floor(fz * 1.5 + 0.5)
return {
  block_under = api:blockAt(bx, by - 1, bz),
  block_ahead = api:blockAt(ax, by, az),
  block_ahead_under = api:blockAt(ax, by - 1, az),
  block_ahead_below2 = api:blockAt(ax, by - 2, az),
}
