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
    }

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
    }
}]).controller('ExportCtrl', ['$scope', '$http', '$stateParams', '$state', 'client', 'esFactory', function($scope, $http, $stateParams, $state, client, esFactory) {
    $scope.selectedFields = {};
    $scope.eIndex = null;
    $scope.eType = "";

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
        if (_.isObject(r._source[key])) {
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

    $scope.$watch('selectedFields', function(newVal) {
        if (newVal) {
            client.search({
                index: $scope.eIndex,
                type: $scope.eType,
                size: 10,
                body: {
                    query: {
                        match_all: {}
                    }
                }
            }, function(err, resp) {
                $scope.results = resp.hits.hits;
                return $scope.error = null;
            });
        }
    });




}]);
