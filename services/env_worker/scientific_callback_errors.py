"""Bounded fatal-error classification shared by scientific callback boundaries."""

def callback_summary(error):
    """Private recovery context: bounded causes, without locals or source reads."""
    pending, seen, rows = [error], set(), []
    while pending and len(seen) < 8:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        try:
            message = str(current)[:200]
        except BaseException as formatting_error:
            if not recoverable_callback(formatting_error):
                raise
            message = 'exception text unavailable'
        kind = type.__getattribute__(type(current), '__name__')[:80]
        rows.append(f'{kind}: {message}')
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(BaseExceptionGroup.exceptions.__get__(current)[:8]))
        cause = BaseException.__cause__.__get__(current)
        if cause is None and not BaseException.__suppress_context__.__get__(current):
            cause = BaseException.__context__.__get__(current)
        if cause is not None:
            pending.append(cause)
    return '\n'.join(rows)[:1800]

def recoverable_callback(error):
    # Qt/psygnal may wrap a fatal exception. Inspect the entire bounded graph,
    # including suppressed contexts and groups, before retaining this process.
    pending, active, complete = [(error, False)], set(), set()
    while pending:
        current, leaving = pending.pop()
        identity = id(current)
        if leaving:
            active.remove(identity)
            complete.add(identity)
            continue
        if identity in complete:
            continue
        if (identity in active or len(active) + len(complete) >= 64
                or not isinstance(current, Exception)
                or isinstance(current, (MemoryError, RecursionError))):
            return False
        active.add(identity)
        pending.append((current, True))
        children = [item for item in (BaseException.__cause__.__get__(current),
                                     BaseException.__context__.__get__(current)) if item is not None]
        if isinstance(current, BaseExceptionGroup):
            members = BaseExceptionGroup.exceptions.__get__(current)
            if len(members) > 64:
                return False
            children.extend(members)
        pending.extend((item, False) for item in children)
    return True

