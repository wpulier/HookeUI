"""Where a verified rejoin (hub.epoch_links) puts a place: one rule for the outlines (hub._carry_over), the cells'
layer (population._ViewOf) and identity, so the board cannot disagree with itself."""


def follow_links(links: dict | None, epoch, dx: float, dy: float, now=None) -> tuple:
    """(epoch, dx, dy) after following every rejoin from `epoch`: each link says "this epoch is that one moved
    by delta". Stops at `now`, the epoch the view is in: never carried past it or out of it (a link out of an
    epoch the view has since come back to is a past one), at an epoch with no link, and on a repeat (a cycle).
    A link without a delta is not followed."""
    seen = set()
    while links and epoch in links and epoch != now and epoch not in seen:
        target, delta = links[epoch][0], links[epoch][1]
        if delta is None:
            break
        seen.add(epoch)
        epoch, dx, dy = target, dx + delta[0], dy + delta[1]
    return epoch, dx, dy
