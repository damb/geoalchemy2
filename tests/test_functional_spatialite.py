from pkg_resources import parse_version
import os
import pytest
import platform
import json

from sqlalchemy import __version__ as SA_VERSION
from sqlalchemy import create_engine, MetaData, Column, Integer
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.event import listen
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import select, func

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement, WKBElement
from geoalchemy2.shape import from_shape, to_shape
from geoalchemy2.compat import str as str_

from shapely.geometry import LineString


if 'SPATIALITE_LIBRARY_PATH' not in os.environ:
    pytest.skip('SPATIALITE_LIBRARY_PATH is not defined, skip SpatiaLite tests',
                allow_module_level=True)

if platform.python_implementation().lower() == 'pypy':
    pytest.skip('skip SpatiaLite tests on PyPy', allow_module_level=True)


def load_spatialite(dbapi_conn, connection_record):
    dbapi_conn.enable_load_extension(True)
    dbapi_conn.load_extension(os.environ['SPATIALITE_LIBRARY_PATH'])
    dbapi_conn.enable_load_extension(False)


engine = create_engine('sqlite:///spatialdb', echo=True)
listen(engine, 'connect', load_spatialite)

metadata = MetaData(engine)
Base = declarative_base(metadata=metadata)


class Lake(Base):
    __tablename__ = 'lake'
    id = Column(Integer, primary_key=True)
    geom = Column(Geometry(geometry_type='LINESTRING', srid=4326,
                           management=True))

    def __init__(self, geom):
        self.geom = geom


session = sessionmaker(bind=engine)()

session.execute('SELECT InitSpatialMetaData()')


class TestInsertionCore():

    def setup(self):
        metadata.drop_all(checkfirst=True)
        metadata.create_all()
        self.conn = engine.connect()

    def teardown(self):
        self.conn.close()
        metadata.drop_all()

    def test_insert(self):
        conn = self.conn

        # Issue two inserts using DBAPI's executemany() method. This tests
        # the Geometry type's bind_processor and bind_expression functions.
        conn.execute(Lake.__table__.insert(), [
            {'geom': 'SRID=4326;LINESTRING(0 0,1 1)'},
            {'geom': WKTElement('LINESTRING(0 0,2 2)', srid=4326)},
            {'geom': from_shape(LineString([[0, 0], [3, 3]]), srid=4326)}
        ])

        results = conn.execute(Lake.__table__.select())
        rows = results.fetchall()

        row = rows[0]
        assert isinstance(row[1], WKBElement)
        wkt = session.execute(row[1].ST_AsText()).scalar()
        assert wkt == 'LINESTRING(0 0, 1 1)'
        srid = session.execute(row[1].ST_SRID()).scalar()
        assert srid == 4326

        row = rows[1]
        assert isinstance(row[1], WKBElement)
        wkt = session.execute(row[1].ST_AsText()).scalar()
        assert wkt == 'LINESTRING(0 0, 2 2)'
        srid = session.execute(row[1].ST_SRID()).scalar()
        assert srid == 4326

        row = rows[2]
        assert isinstance(row[1], WKBElement)
        wkt = session.execute(row[1].ST_AsText()).scalar()
        assert wkt == 'LINESTRING(0 0, 3 3)'
        srid = session.execute(row[1].ST_SRID()).scalar()
        assert srid == 4326


class TestInsertionORM():

    def setup(self):
        metadata.drop_all(checkfirst=True)
        metadata.create_all()

    def teardown(self):
        session.rollback()
        metadata.drop_all()

    def test_WKT(self):
        lake = Lake('LINESTRING(0 0,1 1)')
        session.add(lake)

        with pytest.raises(IntegrityError):
            session.flush()

    def test_WKTElement(self):
        lake = Lake(WKTElement('LINESTRING(0 0,1 1)', srid=4326))
        session.add(lake)
        session.flush()
        session.expire(lake)
        assert isinstance(lake.geom, WKBElement)
        assert str(lake.geom) == '0102000020E6100000020000000000000000000000000000000000000000000' \
                                 '0000000F03F000000000000F03F'
        wkt = session.execute(lake.geom.ST_AsText()).scalar()
        assert wkt == 'LINESTRING(0 0, 1 1)'
        srid = session.execute(lake.geom.ST_SRID()).scalar()
        assert srid == 4326

    def test_WKBElement(self):
        shape = LineString([[0, 0], [1, 1]])
        lake = Lake(from_shape(shape, srid=4326))
        session.add(lake)
        session.flush()
        session.expire(lake)
        assert isinstance(lake.geom, WKBElement)
        assert str(lake.geom) == '0102000020E6100000020000000000000000000000000000000000000000000' \
                                 '0000000F03F000000000000F03F'
        wkt = session.execute(lake.geom.ST_AsText()).scalar()
        assert wkt == 'LINESTRING(0 0, 1 1)'
        srid = session.execute(lake.geom.ST_SRID()).scalar()
        assert srid == 4326


class TestShapely():

    def setup(self):
        metadata.drop_all(checkfirst=True)
        metadata.create_all()

    def teardown(self):
        session.rollback()
        metadata.drop_all()

    def test_to_shape(self):
        lake = Lake(WKTElement('LINESTRING(0 0,1 1)', srid=4326))
        session.add(lake)
        session.flush()
        session.expire(lake)
        lake = session.query(Lake).one()
        assert isinstance(lake.geom, WKBElement)
        assert isinstance(lake.geom.data, str_)
        assert lake.geom.srid == 4326
        s = to_shape(lake.geom)
        assert isinstance(s, LineString)
        assert s.wkt == 'LINESTRING (0 0, 1 1)'
        lake = Lake(lake.geom)
        session.add(lake)
        session.flush()
        session.expire(lake)
        assert isinstance(lake.geom, WKBElement)
        assert isinstance(lake.geom.data, str_)
        assert lake.geom.srid == 4326


class TestCallFunction():

    def setup(self):
        metadata.drop_all(checkfirst=True)
        metadata.create_all()

    def teardown(self):
        session.rollback()
        metadata.drop_all()

    def _create_one_lake(self):
        lake = Lake(WKTElement('LINESTRING(0 0,1 1)', srid=4326))
        session.add(lake)
        session.flush()
        return lake.id

    def test_ST_GeometryType(self):
        lake_id = self._create_one_lake()

        s = select([func.ST_GeometryType(Lake.__table__.c.geom)])
        r1 = session.execute(s).scalar()
        assert r1 == 'LINESTRING'

        lake = session.query(Lake).get(lake_id)
        r2 = session.execute(lake.geom.ST_GeometryType()).scalar()
        assert r2 == 'LINESTRING'

        r3 = session.query(Lake.geom.ST_GeometryType()).scalar()
        assert r3 == 'LINESTRING'

        r4 = session.query(Lake).filter(
            Lake.geom.ST_GeometryType() == 'LINESTRING').one()
        assert isinstance(r4, Lake)
        assert r4.id == lake_id

    def test_ST_Buffer(self):
        lake_id = self._create_one_lake()

        s = select([func.ST_Buffer(Lake.__table__.c.geom, 2)])
        r1 = session.execute(s).scalar()
        assert isinstance(r1, WKBElement)

        lake = session.query(Lake).get(lake_id)
        r2 = session.execute(lake.geom.ST_Buffer(2)).scalar()
        assert isinstance(r2, WKBElement)

        r3 = session.query(Lake.geom.ST_Buffer(2)).scalar()
        assert isinstance(r3, WKBElement)

        assert r1.data == r2.data == r3.data

        r4 = session.query(Lake).filter(
            func.ST_Within(WKTElement('POINT(0 0)', srid=4326),
                           Lake.geom.ST_Buffer(2))).one()
        assert isinstance(r4, Lake)
        assert r4.id == lake_id

    def test_ST_GeoJSON(self):
        lake_id = self._create_one_lake()

        def _test(r):
            r = json.loads(r)
            assert r["type"] == "LineString"
            assert r["coordinates"] == [[0, 0], [1, 1]]

        s = select([func.ST_AsGeoJSON(Lake.__table__.c.geom)])
        r = session.execute(s).scalar()
        _test(r)

        lake = session.query(Lake).get(lake_id)
        r = session.execute(lake.geom.ST_AsGeoJSON()).scalar()
        _test(r)

        r = session.query(Lake.geom.ST_AsGeoJSON()).scalar()
        _test(r)

    @pytest.mark.skipif(
        parse_version(SA_VERSION) < parse_version("1.3.4"),
        reason='Case-insensitivity is only available for sqlalchemy>=1.3.4')
    def test_comparator_case_insensitivity(self):
        lake_id = self._create_one_lake()

        s = select([func.ST_Buffer(Lake.__table__.c.geom, 2)])
        r1 = session.execute(s).scalar()
        assert isinstance(r1, WKBElement)

        lake = session.query(Lake).get(lake_id)

        r2 = session.execute(lake.geom.ST_Buffer(2)).scalar()
        assert isinstance(r2, WKBElement)

        r3 = session.execute(lake.geom.st_buffer(2)).scalar()
        assert isinstance(r3, WKBElement)

        r4 = session.execute(lake.geom.St_BuFfEr(2)).scalar()
        assert isinstance(r4, WKBElement)

        r5 = session.query(Lake.geom.ST_Buffer(2)).scalar()
        assert isinstance(r5, WKBElement)

        r6 = session.query(Lake.geom.st_buffer(2)).scalar()
        assert isinstance(r6, WKBElement)

        r7 = session.query(Lake.geom.St_BuFfEr(2)).scalar()
        assert isinstance(r7, WKBElement)

        assert (
            r1.data == r2.data == r3.data == r4.data == r5.data == r6.data
            == r7.data)
