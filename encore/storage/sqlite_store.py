#
# (C) Copyright 2011 Enthought, Inc., Austin, TX
# All right reserved.
#
# This file is open source software distributed according to the terms in LICENSE.txt
#

"""
Sqlite Store
------------

This is a simple implementation of the key-value store API that lives in a sqlite
database.  Each key is stored in a row which consists of the key, index columns,
metadata and data.  The index columns are a specified subset of the metadata that
can be queried more quickly.

This class is provided in part as a sample implementation of the API.

"""

import cStringIO
import sqlite3
import cPickle
from itertools import izip
from uuid import uuid4

from .abstract_store import AbstractStore
from .utils import buffer_iterator, DummyTransactionContext, StoreProgressManager

def adapt_dict(d):
    return cPickle.dumps(d)

def convert_dict(s):
    return cPickle.loads(s)
    
sqlite3.register_adapter(dict, adapt_dict)
sqlite3.register_converter('dict', convert_dict)

class SqliteStore(AbstractStore):
    """ Sqlite-based Store

    The file-like objects returned by data methods are cStringIO objects.
    
    """
    
    def __init__(self, event_manager, location=':memory:', table='store'):
        self.event_manager = event_manager
        self.location = location
        self.table = table
        
        self._connection = None
    
    def connect(self, credentials=None):
        """ Connect to the key-value store
        
        This connects to the specified location and creates the table, if needed.
        Sqlite has no notion of authentication, so that is not included.

        """
        self._connection = sqlite3.connect(self.location, detect_types=sqlite3.PARSE_DECLTYPES)
        cursor = self._connection.execute(
            "select name from sqlite_master where type='table' and name=?",
            (self.table,)
        )
        if len(cursor.fetchall()) == 0:
            # we need to create the table
            self._connection.execute("""
                create table ? (
                    key text primary key,
                    metadata dict,
                    data blob
                )
                """, self.table)
    
    
    def disconnect(self):
        """ Disconnect from the key-value store
        
        This store does not authenticate, and has no external resources, so this
        does nothing

        """
        self._connection = None


    def info(self):
        """ Get information about the key-value store
        
        Returns
        -------
        
        metadata : dict
            A dictionary of metadata giving information about the key-value store.

        """
        return {
            'location': self.location,
            'table': self.table,
        }
    
    
    def get(self, key):
        """ Retrieve a stream of data and metdata from a given key in the key-value store.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        Returns
        -------
        
        (data, metadata) : tuple of file-like, dict
            A pair of objects, the first being a readable file-like object that
            provides stream of data from the key-value store.  The second is a
            dictionary of metadata for the key.
        
        Raises
        ------
        
        KeyError:
            If the key is not found in the store, a KeyError is raised.

        """
        rows = self._connection.execute(
            'select data, metadata from '+self.table+' where key == ?',
            (key,)
        ).fetchall() # should only ever be zero or one
        if len(rows) == 0:
            raise KeyError(key)
        data = cStringIO.StringIO(rows[0][0])
        metadata = rows[0][1]
        return data, metadata
    
    
    def set(self, key, value, buffer_size=1048576):
        """ Store a stream of data into a given key in the key-value store.
        
        This may be left unimplemented by subclasses that represent a read-only
        key-value store.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        value : tuple of file-like, dict
            A pair of objects, the first being a readable file-like object that
            provides stream of data from the key-value store.  The second is a
            dictionary of metadata for the key.
           
        buffer_size : int
            An optional indicator of the number of bytes to read at a time.
            Implementations are free to ignore this hint or use a different
            default if they need to.  The default is 1048576 bytes (1 MiB).
     
        """
        data, metadata = value
        self._metadata[key] = metadata.copy()
        self.set_data(key, data, buffer_size)

    def delete(self, key):
        """ Delete a key from the repsository.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        """
        del self._data[key]
        metadata = self._metadata.pop(key)
        self.event_manager.emit(StoreDeleteEvent(self, key=key, metadata=metadata))
    
    
    def exists(self, key):
        """ Test whether or not a key exists in the key-value store
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        Returns
        -------
        
        exists : bool
            Whether or not the key exists in the key-value store.
        
        """
        return key in self._data and key in self._metadata


    def get_data(self, key):
        """ Retrieve a stream from a given key in the key-value store.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        Returns
        -------
        
        data : file-like
            A readable file-like object the that provides stream of data from the
            key-value store.

        """
        rows = self._connection.execute(
            'select data from '+self.table+' where key == ?',
            (key,)
        ).fetchall() # should only ever be zero or one
        if len(rows) == 0:
            raise KeyError(key)
        data = cStringIO.StringIO(rows[0][0])
        return data


    def get_metadata(self, key, select=None):
        """ Retrieve the metadata for a given key in the key-value store.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        select : iterable of strings or None
            Which metadata keys to populate in the result.  If unspecified, then
            return the entire metadata dictionary.
        
        Returns
        -------
        
        metadata : dict
            A dictionary of metadata associated with the key.  The dictionary
            has keys as specified by the metadata_keys argument.
        
        Raises
        ------
        
        KeyError:
            This will raise a key error if the key is not present in the store,
            and if any metadata key is requested which is not present in the
            metadata.

        """
        rows = self._connection.execute(
            'select metadata from '+self.table+' where key == ?',
            (key,)
        ).fetchall() # should only ever be zero or one
        if len(rows) == 0:
            raise KeyError(key)
        metadata = rows[0][0]
        if select is not None:
            return dict((metadata_key, metadata[metadata_key])
                for metadata_key in select if metadata_key in metadata)
        return metadata.copy()
    
    
    def set_data(self, key, data, buffer_size=1048576):
        """ Replace the data for a given key in the key-value store.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        data : file-like
            A readable file-like object the that provides stream of data from the
            key-value store.

        """
        update = key in self._data
        metadata = self._metadata.get(key, None)
        with StoreProgressManager(self.event_manager, self, uuid4(),
                "Setting data into '%s'" % (key,), -1,
                key=key, metadata=metadata) as progress:
            chunks = list(buffer_iterator(data, buffer_size, progress))
            self._data[key] = b''.join(chunks)
        if update:
            self.event_manager.emit(StoreUpdateEvent(self, key=key, metadata=metadata))
        else:
            self.event_manager.emit(StoreSetEvent(self, key=key, metadata=metadata))
    
    
    def set_metadata(self, key, metadata):
        """ Set new metadata for a given key in the key-value store.
        
        This replaces the existing metadata set for the key with a new set of
        metadata.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        metadata : dict
            A dictionary of metadata to associate with the key.  The dictionary
            keys should be strings which are valid Python identifiers.

        """
        update = key in self._metadata
        self._metadata[key] = metadata.copy()
        if update:
            self.event_manager.emit(StoreUpdateEvent(self, key=key, metadata=metadata))
        else:
            self.event_manager.emit(StoreSetEvent(self, key=key, metadata=metadata))


    def update_metadata(self, key, metadata):
        """ Update the metadata for a given key in the key-value store.
        
        This performs a dictionary update on the existing metadata with the
        provided metadata keys and values
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        metadata : dict
            A dictionary of metadata to associate with the key.  The dictionary
            keys should be strings which are valid Python identifiers.

        """
        self._metadata[key].update(metadata)
        self.event_manager.emit(StoreUpdateEvent(self, key=key, metadata=self._metadata[key]))
   
   
    def multiget(self, keys):
        """ Retrieve the data and metadata for a collection of keys.
        
        Parameters
        ----------
        
        keys : iterable of strings
            The keys for the resources in the key-value store.  Each key is a
            unique identifier for a resource within the key-value store.
        
        Returns
        -------
        
        result : iterator of (file-like, dict) tuples
            An iterator of (data, metadata) pairs.
        
        Raises
        ------
        
        KeyError:
            This will raise a key error if the key is not present in the store.
        
        """
        return super(SqliteStore, self).multiget(keys)
    
   
    def multiget_data(self, keys):
        """ Retrieve the data for a collection of keys.
        
        Parameters
        ----------
        
        keys : iterable of strings
            The keys for the resources in the key-value store.  Each key is a
            unique identifier for a resource within the key-value store.
        
        Returns
        -------
        
        result : iterator of file-like
            An iterator of file-like data objects corresponding to the keys.
        
        Raises
        ------
        
        KeyError:
            This will raise a key error if the key is not present in the store.
        
        """
        return super(SqliteStore, self).multiget_data(keys)
    

    def multiget_metadata(self, keys, select=None):
        """ Retrieve the metadata for a collection of keys in the key-value store.
        
        Parameters
        ----------
        
        keys : iterable of strings
            The keys for the resources in the key-value store.  Each key is a
            unique identifier for a resource within the key-value store.
        
        select : iterable of strings or None
            Which metadata keys to populate in the results.  If unspecified, then
            return the entire metadata dictionary.
        
        Returns
        -------
        
        metadatas : iterator of dicts
            An iterator of dictionaries of metadata associated with the key.
            The dictionaries have keys as specified by the select argument.  If
            a key specified in select is not present in the metadata, then it
            will not be present in the returned value.
        
        Raises
        ------
        
        KeyError:
            This will raise a key error if the key is not present in the store.
        
        """
        return super(SqliteStore, self).multiget_metadata(keys, select)
    
      
    def multiset(self, keys, values, buffer_size=1048576):
        """ Set the data and metadata for a collection of keys.
        
        Where supported by an implementation, this should perform the whole
        collection of sets as a single transaction.
        
        Like zip() if keys and values have different lengths, then any excess
        values in the longer list should be silently ignored.
        
        Parameters
        ----------
        
        keys : iterable of strings
            The keys for the resources in the key-value store.  Each key is a
            unique identifier for a resource within the key-value store.
        
        values : iterable of (file-like, dict) tuples
            An iterator that provides the (data, metadata) pairs for the
            corresponding keys.
           
        buffer_size : int
            An optional indicator of the number of bytes to read at a time.
            Implementations are free to ignore this hint or use a different
            default if they need to.  The default is 1048576 bytes (1 MiB).
        
        """
        return super(SqliteStore, self).multiset(keys, values, buffer_size)
    
   
    def multiset_data(self, keys, datas, buffer_size=1048576):
        """ Set the data and metadata for a collection of keys.
        
        Where supported by an implementation, this should perform the whole
        collection of sets as a single transaction.
                
        Like zip() if keys and datas have different lengths, then any excess
        values in the longer list should be silently ignored.

        Parameters
        ----------
        
        keys : iterable of strings
            The keys for the resources in the key-value store.  Each key is a
            unique identifier for a resource within the key-value store.
        
        datas : iterable of file-like objects
            An iterator that provides the data file-like objects for the
            corresponding keys.
           
        buffer_size : int
            An optional indicator of the number of bytes to read at a time.
            Implementations are free to ignore this hint or use a different
            default if they need to.  The default is 1048576 bytes (1 MiB).
        
        """
        return super(SqliteStore, self).multiset_data(keys, datas, buffer_size)
    
   
    def multiset_metadata(self, keys, metadatas):
        """ Set the data and metadata for a collection of keys.
        
        Where supported by an implementation, this should perform the whole
        collection of sets as a single transaction.
                
        Like zip() if keys and metadatas have different lengths, then any excess
        values in the longer list should be silently ignored.

        Parameters
        ----------
        
        keys : iterable of strings
            The keys for the resources in the key-value store.  Each key is a
            unique identifier for a resource within the key-value store.
        
        metadatas : iterable of dicts
            An iterator that provides the metadata dictionaries for the
            corresponding keys.
        
        """
        return super(SqliteStore, self).multiset_metadata(keys, metadatas)


    def multiupdate_metadata(self, keys, metadatas):
        """ Update the metadata for a collection of keys.
        
        Where supported by an implementation, this should perform the whole
        collection of sets as a single transaction.
                
        Like zip() if keys and metadatas have different lengths, then any excess
        values in the longer list should be silently ignored.

        Parameters
        ----------
        
        keys : iterable of strings
            The keys for the resources in the key-value store.  Each key is a
            unique identifier for a resource within the key-value store.
        
        metadatas : iterable of dicts
            An iterator that provides the metadata dictionaries for the
            corresponding keys.
        
        """
        return super(SqliteStore, self).multiupdate_metadata(keys, metadatas)
    
   
    def transaction(self, notes):
        """ Provide a transaction context manager
        
        This class does not support transactions, so it returns a dummy object.

        """
        return DummyTransactionContext()


    def query(self, select=None, **kwargs):
        """ Query for keys and metadata matching metadata provided as keyword arguments
        
        This provides a very simple querying interface that returns precise
        matches with the metadata.  If no arguments are supplied, the query
        will return the complete set of metadata for the key-value store.
        
        Parameters
        ----------
        
        select : iterable of strings or None
            An optional list of metadata keys to return.  If this is not None,
            then the metadata dictionaries will only have values for the specified
            keys populated.
        
        **kwargs :
            Arguments where the keywords are metadata keys, and values are
            possible values for that metadata item.

        Returns
        -------
        
        result : iterable
            An iterable of keys, metadata tuples where metadata matches
            all the specified values for the specified metadata keywords.
        
        """
        cursor = self._connection.execute('select key, metadata from '+self.table)
        if select is not None:
            for key, metadata in cursor:
                if all(metadata.get(arg) == value for arg, value in kwargs.items()):
                    yield key, dict((metadata_key, metadata[metadata_key])
                        for metadata_key in select if metadata_key in metadata)
        else:
            for key, metadata in cursor:
                if all(metadata.get(arg) == value for arg, value in kwargs.items()):
                    yield key, metadata.copy()
    
    
    def query_keys(self, **kwargs):
        """ Query for keys matching metadata provided as keyword arguments
        
        This provides a very simple querying interface that returns precise
        matches with the metadata.  If no arguments are supplied, the query
        will return the complete set of keys for the key-value store.
        
        This is equivalent to self.query(**kwargs).keys(), but potentially
        more efficiently implemented.
        
        Parameters
        ----------
        
        **kwargs :
            Arguments where the keywords are metadata keys, and values are
            possible values for that metadata item.

        Returns
        -------
        
        result : iterable
            An iterable of key-value store keys whose metadata matches all the
            specified values for the specified metadata keywords.
        
        """
        return super(SqliteStore, self).query_keys(**kwargs)


    def glob(self, pattern):
        """ Return keys which match glob-style patterns
        
        Parameters
        ----------
        
        pattern : string
            Glob-style pattern to match keys with.

        Returns
        -------
        
        result : iterable
            A iterable of keys which match the glob pattern.
        
        """
        return super(SqliteStore, self).glob(pattern)

        
    def to_file(self, key, path, buffer_size=1048576):
        """ Efficiently store the data associated with a key into a file.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        path : string
            A file system path to store the data to.
        
        buffer_size : int
            This is ignored.
        
        """
        return super(SqliteStore, self).to_file(key, path, buffer_size)
    
    
    def from_file(self, key, path, buffer_size=1048576):
        """ Efficiently read data from a file into a key in the key-value store.
        
        This makes no attempt to set metadata.
                
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        path : string
            A file system path to read the data from.
        
        buffer_size : int
            This is ignored.
        
        """
        return super(SqliteStore, self).from_file(key, path, buffer_size)

    def to_bytes(self, key, buffer_size=1048576):
        """ Efficiently store the data associated with a key into a bytes object.
        
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        buffer_size : int
            This is ignored.
        
        """
        return super(SqliteStore, self).to_bytes(key, buffer_size)
        
    def from_bytes(self, key, data, buffer_size=1048576):
        """ Efficiently read data from a bytes object into a key in the key-value store.
        
        This makes no attempt to set metadata.
                
        Parameters
        ----------
        
        key : string
            The key for the resource in the key-value store.  They key is a unique
            identifier for the resource within the key-value store.
        
        data : bytes
            The data as a bytes object.
        
        buffer_size : int
            This is ignored.
        
        """
        return super(SqliteStore, self).from_bytes(key, data, buffer_size)

