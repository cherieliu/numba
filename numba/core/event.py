import abc
import enum
import time
from timeit import default_timer as timer
from contextlib import contextmanager, ExitStack
from collections import defaultdict


class EventStatus(enum.Enum):
    """Status of an event.
    """
    START = enum.auto()
    END = enum.auto()


# Builtin event kinds.
_builtin_kinds = frozenset([
    "numba:compiler_lock",
    "numba:compile",
])


def _guard_kind(kind):
    """Guard that event kind is valid.

    All event kind with a "numba:" prefix must be defined in pre-defined.
    Custom event kind is allowed by not using the above prefix.

    Parameters
    ----------
    kind : str

    Return
    ------
    res : str
    """
    if kind.startswith("numba:") and kind not in _builtin_kinds:
        raise ValueError(f"{kind} is not a valid event kind")
    return kind


class Event:
    """An event.
    """
    def __init__(self, kind, status, data=None, exc_details=None):
        """
        Parameters
        ----------
        kind : str
        status : EventStatus
        data : any; optional
            Additional data for the event.
        exc_details : 3-tuple; optional
            Same 3-tuple for ``__exit__``.
        """
        self._kind = _guard_kind(kind)
        self._status = status
        self._data = data
        self._exc_details = exc_details

    @property
    def kind(self):
        """Event kind

        Returns
        -------
        res : str
        """
        return self._kind

    @property
    def status(self):
        """Event status

        Returns
        -------
        res : EventStatus
        """
        return self._status

    @property
    def data(self):
        """Event data

        Returns
        -------
        res : object
        """
        return self._data

    @property
    def is_start(self):
        """Is it a *START* event?

        Returns
        -------
        res : bool
        """
        return self._status == EventStatus.START

    @property
    def is_end(self):
        """Is it an *END* event?

        Returns
        -------
        res : bool
        """
        return self._status == EventStatus.END

    @property
    def is_failed(self):
        """Does the event carrying an exception?

        This is used for *END* event. This method will never return ``True``
        in a *START* event.

        Returns
        -------
        res : bool
        """
        return self._exc_details[0] is None

    def __str__(self):
        data = (f"{type(self.data).__qualname__}"
                if self.data is not None else "None")
        return f"Event({self._kind}, {self._status}, data: {data})"


_registered = defaultdict(list)


def register(kind, listener):
    """Register a listener for a given event kind.

    Parameters
    ----------
    kind : str
    listener : Listener
    """
    assert isinstance(listener, Listener)
    kind = _guard_kind(kind)
    _registered[kind].append(listener)


def unregister(kind, listener):
    """Unregister a listener for a given event kind.

    Parameters
    ----------
    kind : str
    listener : Listener
    """
    assert isinstance(listener, Listener)
    kind = _guard_kind(kind)
    lst = _registered[kind]
    lst.remove(listener)


def broadcast(event):
    """Broadcast an event to all registered listeners.

    Parameters
    ----------
    event : Event
    """
    for listener in _registered[event.kind]:
        listener.notify(event)


class Listener(abc.ABC):
    """Base class for all event listeners.
    """
    @abc.abstractmethod
    def on_start(self, event):
        """Called when there is a *START* event.

        Parameters
        ----------
        event : Event
        """
        pass

    @abc.abstractmethod
    def on_end(self, event):
        """Called when there is a *END* event.

        Parameters
        ----------
        event : Event
        """
        pass

    def notify(self, event):
        """Notify this Listener with the given Event.
        """
        if event.is_start:
            self.on_start(event)
        elif event.is_end:
            self.on_end(event)
        else:
            raise AssertionError("unreachable")


class TimingListener(Listener):
    """A listener that measures the duration between *START* and *END* events.
    """
    def __init__(self):
        self._ts = None
        self._depth = 0

    def on_start(self, event):
        if self._ts is None:
            self._ts = timer()
        self._depth += 1

    def on_end(self, event):
        self._depth -= 1
        if self._depth == 0:
            self._duration = timer() - self._ts

    @property
    def duration(self):
        """Returns the measured duration.
        """
        return self._duration


class RecordingListener(Listener):
    """A listener that records all event and store it in the ``.buffer``
    attribute as a list of 2-tuple ``(float, Event)``, where the first element
    is the time of the event as returned by ``time.time()`` and the second
    element is the event.
    """
    def __init__(self):
        self.buffer = []

    def on_start(self, event):
        self.buffer.append((time.time(), event))

    def on_end(self, event):
        self.buffer.append((time.time(), event))


@contextmanager
def install_timer(kind, callback):
    """Install a TimingListener temporarily to measure the duration for
    an event.

    If the context completes successfully, the *callback* function is executed.
    The *callback* function is expected to take a float argument for the
    duration in seconds.

    Returns
    -------
    res : TimingListener
    """
    listener = TimingListener()
    register(kind, listener)
    try:
        yield listener
    finally:
        unregister(kind, listener)
    callback(listener.duration)


@contextmanager
def install_recorder(kind):
    """Install a RecordingListener temporarily to record all events.

    The buffer is filled from the context is closed.

    Returns
    -------
    res : RecordingListener
    """
    rl = RecordingListener()
    register(kind, rl)
    try:
        yield rl
    finally:
        unregister(kind, rl)


def start_event(kind, data=None):
    """Signal the start of an event of *kind* with *data*.

    Parameters
    ----------
    kind : str
        Event kind.
    data : any; optional
        Extra event data.
    """
    evt = Event(kind=kind, status=EventStatus.START, data=data)
    broadcast(evt)


def end_event(kind, data=None, exc_details=None):
    """Signal the end of an event of *kind*, *exc_details*.

    Parameters
    ----------
    kind : str
        Event kind.
    data : any; optional
        Extra event data.
    """
    evt = Event(
        kind=kind, status=EventStatus.END, data=data, exc_details=exc_details,
    )
    broadcast(evt)


@contextmanager
def mark_event(kind, data=None):
    """A context manager to signal the start and end events of *kind* with
    *data*.

    Parameters
    ----------
    kind : str
        Event kind.
    data : any; optional
        Extra event data.
    """
    with ExitStack() as scope:
        @scope.push
        def on_exit(*exc_details):
            end_event(kind, data=data, exc_details=exc_details)

        start_event(kind, data=data)
        yield
