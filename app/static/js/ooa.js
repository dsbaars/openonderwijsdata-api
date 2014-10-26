'use strict';

// Declare app level module which depends on views, and components
angular.module('myApp', ['ui.router', 'elasticsearch'], function($stateProvider, $urlRouterProvider, $locationProvider) {
    $urlRouterProvider.otherwise("/");
    $locationProvider.html5Mode(true);
});

angular.module('myApp').service("client", function(esFactory) {
    return esFactory({
        host: "localhost:9200",
        apiVersion: "1.3",
        log: "trace"
    });
});

angular.module('myApp').config(function($stateProvider, $urlRouterProvider) {

    $stateProvider
    .state('index', {
        url: "/",
        templateUrl: "static/home.html",
        controller: 'HomeCtrl'
    })
    .state('stats', {
        url: "/stats",
        templateUrl: "static/stats.html"
    })
    .state('export', {
        url: "/export",
        templateUrl: "static/export.html",
        controller: 'ExportCtrl'

    })
    .state('search', {
        url: "/search?q",
        templateUrl: "static/search.html",
        controller: 'SearchCtrl'
    });

});

angular.module('myApp').controller('HomeCtrl', ['$scope', '$http', '$state', function($scope, $http, $state) {

    $scope.onFormSubmit = function () {
        $state.go('search', {q: $scope.query});
    };

}]).controller('SearchCtrl', ['$scope', '$http', '$stateParams', '$state', function($scope, $http, $stateParams, $state) {
    $scope.query = $stateParams.q;

    $http({method: 'GET', url: 'http://localhost:5001/api/v1/search', params: { q: $stateParams.q }}).
    success(function(data,status,headers,config) {
        $scope.results = data.hits;
        $scope.took = data.took;
        $scope.total = data.total;
    });

    $scope.onFormSubmit = function () {
        $state.go('search', {q: $scope.query});
    };
}]).controller('ExportCtrl', ['$scope', '$http', '$stateParams', '$state', 'client', 'esFactory', function($scope, $http, $stateParams, $state, client, esFactory) {
    $scope.selectedFields = {};
    $scope.eIndex = null;
    $scope.eType = "";
    $scope.ref_year = 2014;
    $scope.result_limit = 10;

    $http({method: 'GET', url: 'http://localhost:5001/api/v1/indexes'}).success(function(data,status,headers,config) {
        $scope.indexes = data;
    });

    $scope.$watch('eIndex', function(newVal) {
        if (newVal) {
            $http({method: 'GET', url: 'http://localhost:5001/api/v1/types/' + newVal }).success(function(data,status,headers,config) {
                $scope.types = data;
                $scope.eType = "";
            //    $scope.$apply();
            });
        }
    });

    $scope.$watch('eType', function(newVal) {
        if (newVal)
            client.indices.getMapping({
                'index' : $scope.eIndex,
                'type' : newVal
            }, function(err, resp) {
                //$scope.clusterState = resp;
                $scope.fields = resp[$scope.eIndex].mappings[$scope.eType].properties;
                $scope.selectedFields = {};
            });
    });

    $scope.get = function(r, key) {

        if (_.isArray(r._source[key])) {
            return r._source[key].join(',');
        } else if (_.isObject(r._source[key])) {
            var ret = "";
            _.forEach($scope.selectedFields[key], function(val, ky, col) {
                //console.log(ky);
                if (val) {
                    ret += r._source[key][ky] + ', ';
                }
            });

            return ret;
        } else {
            return r._source[key];
        }
    };

    $scope.$watchCollection('[selectedFields, result_limit]', function(newVal) {
        if (newVal, $scope.eType) {
            client.search({
                index: $scope.eIndex,
                type: $scope.eType,
                size: $scope.result_limit,
                body: {
                    query: {
                        match_all: {}
                    },
                    filter: {
                        bool: {
                            must: {
                                term: {
                                    reference_year: $scope.ref_year
                                }

                            }
                        }


                    }
                }
            }, function(err, resp) {
                $scope.results = resp.hits.hits;
                $scope.hits = resp.hits.total;
                return $scope.error = null;
            });
        }
    });

    $scope.exportToXLSX = function()
    {
        function sheet_from_array_of_arrays(data, opts) {
        	var ws = {};
        	var range = {s: {c:10000000, r:10000000}, e: {c:0, r:0 }};
        	for(var R = 0; R != data.length; ++R) {
        		for(var C = 0; C != data[R].length; ++C) {
        			if(range.s.r > R) range.s.r = R;
        			if(range.s.c > C) range.s.c = C;
        			if(range.e.r < R) range.e.r = R;
        			if(range.e.c < C) range.e.c = C;
        			var cell = {v: data[R][C] };
        			if(cell.v == null) continue;
        			var cell_ref = XLSX.utils.encode_cell({c:C,r:R});

        			cell.t = 's';

        			ws[cell_ref] = cell;
        		}
        	}
        	if(range.s.c < 10000000) ws['!ref'] = XLSX.utils.encode_range(range);
        	return ws;
        }

        /* original data */
        var rows = $("tr",$("table")).map(function() {
            return [$("td,th",this).map(function() {
                return $(this).text().trim();
            }).get()];
        }).get();

        var data = [['a', 'b', 'c']];
        var ws_name = "SheetJS";

        function Workbook() {
        	if(!(this instanceof Workbook)) return new Workbook();
        	this.SheetNames = [];
        	this.Sheets = {};
        }

        var wb = new Workbook(), ws = sheet_from_array_of_arrays(rows);

        /* add worksheet to workbook */
        wb.SheetNames.push(ws_name);
        wb.Sheets[ws_name] = ws;

        /* write file */
        var wbout = XLSX.write(wb, {bookType:'xlsx', bookSST:true, type: 'binary'});

        function s2ab(s) {
        	var buf = new ArrayBuffer(s.length);
        	var view = new Uint8Array(buf);
        	for (var i=0; i!=s.length; ++i) view[i] = s.charCodeAt(i) & 0xFF;
        	return buf;
        }
        saveAs(new Blob([s2ab(wbout)],{type:"application/octet-stream"}), "test.xlsx")
    }


}]);
